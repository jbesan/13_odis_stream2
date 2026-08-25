"""Toolless adaptive parent agent for the social-integration pilot."""

from dataclasses import dataclass

from pydantic_ai import Agent, ModelRetry, RunContext

from agents.agent_config import create_agent
from agents.evidence.executor import validate_plan_arguments
from agents.evidence.projector import ExpertEvidencePacket
from agents.evidence.registry import SOCIAL_TOOL_REGISTRY
from core.evidence import EvidencePlan, ExpertStep, FinalExpertReport, ToolSpecView


@dataclass(frozen=True)
class ExpertRunDeps:
    packet: ExpertEvidencePacket
    phase: str
    tool_specs: tuple[ToolSpecView, ...]
    allowed_evidence_ids: frozenset[str]
    allowed_gap_ids: frozenset[str] = frozenset()
    evidence_delta: str = ""


ADAPTIVE_SOCIAL_PROMPT = """
Tu es l'expert Intégration Sociale OD&IS au service d'un Travailleur Social.
Reste strictement sur les associations d'aide, l'accueil des personnes réfugiées,
les cours de français/FLE, les loisirs et le sport, et l'inclusion locale. La
santé, le logement, la mobilité, l'éducation et l'emploi sont hors de ton périmètre
et sont traités par d'autres experts. Ne recherche jamais le CCAS.

Le parent ne possède aucun outil exécutable. Au premier passage:
- retourne FinalExpertReport si les faits du dossier suffisent réellement;
- sinon retourne un seul EvidencePlan contenant toutes les recherches indépendantes
  nécessaires, au maximum quatre.

Chaque lacune avec resolution_strategy="trusted_tool" doit être reliée à au moins
une trusted_request, et chaque trusted_request doit viser exactement une lacune.
Regroupe dans ses arguments les requêtes indépendantes utiles à cette lacune.
Utilise search_places_batch_tool pour les services locaux,
les cours de FLE et les équipements; utilise search_rna_rag_batch_tool pour les
associations, l'entraide, l'inclusion, les loisirs et le sport. Une lacune sans
outil n'est admise qu'avec resolution_strategy="manual" et une étape manuelle
précise, ou resolution_strategy="out_of_scope". N'invente jamais un tool_id ni
un evidence_id. Le web n'est qu'un fallback pré-déclaré, autorisé uniquement
après not_found ou unavailable d'un outil de confiance.

En phase finalize, retourne obligatoirement FinalExpertReport. Ne restitue pas
l'inventaire de tout ce qui a été trouvé. Sélectionne uniquement ce qui est utile
à la mission et au profil du bénéficiaire, puis formule au maximum quatre éléments
d'analyse expliquant ce que les faits impliquent pour l'accompagnement. Propose
au maximum quatre actions concrètes et prioritaires.

Chaque élément d'analyse et chaque action doit référencer ses evidence_ids ou ses
gap_ids dans les champs structurés. Une interprétation doit être marquée inferred;
une incertitude doit être marquée uncertain. Ne transforme jamais not_found,
partial, timeout ou unavailable en preuve d'absence.

Le statut des lacunes appartient exclusivement à l'application. Tu ne peux ni
fermer ni résoudre une lacune: utilise les gap_ids uniquement pour expliquer une
incertitude ou justifier une action de vérification. Les sources détaillées seront
affichées séparément par l'application; n'insère pas les IDs dans le texte visible.
"""


adaptive_social_integration_agent: Agent[ExpertRunDeps, ExpertStep] = create_agent(
    "social_integration_expert",
    name="social_integration_expert_adaptive",
    deps_type=ExpertRunDeps,
    output_type=ExpertStep,
)


@adaptive_social_integration_agent.system_prompt
def adaptive_social_prompt(ctx: RunContext[ExpertRunDeps]) -> str:
    deps = ctx.deps
    specs = "\n".join(
        f"- {spec.tool_id}: {spec.description}; schéma={spec.input_schema}"
        for spec in deps.tool_specs
    )
    return (
        f"{ADAPTIVE_SOCIAL_PROMPT}\nPhase immuable: {deps.phase}\n\n"
        f"{deps.packet.as_prompt()}\n\nCatalogue lecture seule:\n{specs}\n\n"
        "Preuves nouvelles et statuts de lacunes calculés par l'application:\n"
        f"{deps.evidence_delta or 'Aucune recherche exécutée dans cette phase.'}"
    )


@adaptive_social_integration_agent.output_validator
def validate_adaptive_output(
    ctx: RunContext[ExpertRunDeps], output: ExpertStep
) -> ExpertStep:
    deps = ctx.deps
    if deps.phase == "finalize" and not isinstance(output, FinalExpertReport):
        raise ModelRetry("La phase finalize exige FinalExpertReport.")
    if deps.phase == "assess" and isinstance(output, EvidencePlan):
        allowed_tools = {spec.tool_id for spec in deps.tool_specs}
        validation_registry = {
            tool_id: registered
            for tool_id, registered in SOCIAL_TOOL_REGISTRY.items()
            if tool_id in allowed_tools
        }
        try:
            validate_plan_arguments(
                output,
                target_codgeo=deps.packet.target_codgeo,
                target_city_name=deps.packet.target_city_name,
                registry=validation_registry,
            )
        except ValueError as exc:
            raise ModelRetry(str(exc)) from exc

        working_citations = {
            evidence_id
            for finding in output.working_state.findings
            for evidence_id in finding.evidence_ids
        }
        unknown = working_citations - set(deps.allowed_evidence_ids)
        if unknown:
            raise ModelRetry(f"Evidence IDs inconnus: {sorted(unknown)}")
        if any(not finding.evidence_ids for finding in output.working_state.findings):
            raise ModelRetry("Chaque finding de travail doit citer un evidence_id.")
    if isinstance(output, FinalExpertReport):
        cited = {
            evidence_id
            for item in [*output.analysis, *output.recommended_actions]
            for evidence_id in item.evidence_ids
        }
        unknown = cited - set(deps.allowed_evidence_ids)
        if unknown:
            raise ModelRetry(f"Evidence IDs inconnus: {sorted(unknown)}")
        report_gap_ids = {
            gap_id
            for item in [*output.analysis, *output.recommended_actions]
            for gap_id in item.gap_ids
        }
        if not report_gap_ids <= set(deps.allowed_gap_ids):
            raise ModelRetry("Le rapport final contient un gap_id non planifié.")
        unsupported_analysis = [
            insight.text
            for insight in output.analysis
            if not insight.evidence_ids and not insight.gap_ids
        ]
        if unsupported_analysis:
            raise ModelRetry(
                "Chaque élément d'analyse doit référencer une preuve ou une lacune."
            )
        if any(
            insight.status in {"supported", "inferred"} and not insight.evidence_ids
            for insight in output.analysis
        ):
            raise ModelRetry(
                "Une analyse supported ou inferred exige au moins un evidence_id."
            )
        if any(
            not action.evidence_ids and not action.gap_ids
            for action in output.recommended_actions
        ):
            raise ModelRetry("Chaque action doit référencer une preuve ou une lacune.")
    return output
