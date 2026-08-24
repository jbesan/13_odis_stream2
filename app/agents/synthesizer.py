"""
Composite Synthesizer Agent (Phase 1).

Generates concise Executive Overviews (150–250 words) and Actionable Next Steps
using pre-digested artifact snippets, without rephrasing or duplicating full expert reports.
"""

import logging
from typing import Optional, Union
from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext
from .state import ODISDeps, GraphState
from .agent_config import create_agent, get_swarm_boilerplate

logger = logging.getLogger("synthesizer_agent")


class SynthesizerResult(BaseModel):
    """Structured output for the decoupled composite synthesizer."""

    avis_global: str = Field(
        description="Avis Global d'Orientation (150 à 200 mots : adéquation globale entre besoins du bénéficiaire et atouts de la commune, points cardinaux en gras)"
    )
    analyse_comparative: str = Field(
        description="Analyse comparative territoriale digérée : un court tableau Markdown des écarts clés (3 à 5 lignes max) suivi d'une phrase de synthèse qualitative / conclusion pour le bénéficiaire, en s'appuyant strictement sur les chiffres fournis sans recalculer."
    )
    elements_non_verifies: Optional[str] = Field(
        default=None,
        description="Section propre détaillant les éléments non vérifiés, données manquantes ou vigilances identifiées par les experts (ou None si tout a été vérifié)",
    )
    et_ensuite: str = Field(
        description="Pistes d'action concrètes et immédiates pour le travailleur social (2 à 3 démarches sous forme de liste à puces)"
    )


# --- Synthesis Prompts (Decoupled & Compact) ---

SYNTH_SYSTEM_PROMPT_ANALYSIS = """
{SWARM_BOILERPLATE}
**Rôle** : Synthétiseur final du swarm d'agents IA thématiques. Ta mission est d'émettre un **Avis Global d'Orientation** concis et actionnable pour le Travailleur Social qui accompagne le bénéficiaire dans sa relocalisation.

> **RÈGLE CRITIQUE DE DÉCOUPLAGE** :
> Ne réécris PAS et ne résume PAS en détail les rapports des experts thématiques (logement, santé, emploi, etc.). Leurs fiches détaillées seront affichées telles quelles dans l'application. 
> Concentre-toi EXCLUSIVEMENT sur la vision d'ensemble stratégique et les arbitrages clés.

> **RÈGLE CRITIQUE COMPARATIF TERRITORIAL** :
> Utilise STRICTEMENT les écarts, pourcentages et chiffres fournis dans la section "Comparatif territorial" calculés par le comparateur déterministe.
> Il est STRICTEMENT INTERDIT de recalculer des métriques ou d'inventer des chiffres.
> Ton rôle est de restituer ces écarts de manière digérée (un court tableau Markdown synthétique des points clés + 1 phrase de synthèse sur ce que cela implique concrètement pour le projet de vie du bénéficiaire).

# Éléments de synthèse pré-digérés :
{COMPOSITE_CONTEXT}

# Instructions de renseignement des champs structurés :
1. **avis_global** : Rédige 150 à 200 mots percutants sur l'adéquation globale entre le profil du bénéficiaire et les atouts de {FOCUS_CITY}. Mets en gras les arbitrages majeurs.
2. **analyse_comparative** : Produis une synthèse comparative claire et digérée par rapport à {CURRENT_CITY_NAME} : un court tableau Markdown (3 à 5 critères majeurs : Critère, {FOCUS_CITY}, {CURRENT_CITY_NAME}, Écart relatif) suivi d'une phrase concise sur le gain territorial ou les arbitrages pour le bénéficiaire.
3. **elements_non_verifies** : Si les experts ont signalé des éléments non vérifiés, données manquantes ou vigilances, regroupe-les ici de manière claire et factuelle (ou laisse à None si aucun manque n'est signalé).
4. **et_ensuite** : Liste 2 à 3 démarches concrètes et prioritaires pour le travailleur social.
"""

SYNTH_SYSTEM_PROMPT_SPECIFIC = """
{SWARM_BOILERPLATE}
**Rôle** : Synthétiseur final du swarm d'agents IA thématiques. Ta mission est de répondre UNIQUEMENT et précisément à la question spécifique du Travailleur Social en exploitant les synthèses des experts.

# Éléments de synthèse pré-digérés :
{COMPOSITE_CONTEXT}

# Instructions :
- Réponds UNIQUEMENT et de manière ciblée à la question : "{LAST_MESSAGE}"
- Sois factuel, concis et précis. Si des informations manquent ou sont incertaines dans les retours experts, signale-le explicitement.
- Mets en gras les entités clés (adresses, téléphones, structures).
"""


def build_composite_synthesis_context(state: GraphState) -> str:
    """Builds a compact, pre-digested context representation for the Synthesizer.

    Replaces large raw JSON trees (~11.4k tokens) with structured text snippets (~2.5k tokens).
    """
    sections = []

    # 1. Beneficiary Profile & Search Criteria Briefing
    focus_city = None
    if state.search_results and state.focus_city and state.focus_city.codgeo:
        focus_city = state.search_results.get_by_code(state.focus_city.codgeo)
    if not focus_city:
        focus_city = state.focus_city

    focus_name = focus_city.name if focus_city else "Commune cible"
    brief = state.odis_brief or "Aucun brief spécifique renseigné."

    sections.append(
        f"### 👤 Profil & Besoins du Bénéficiaire\n"
        f"- **Commune analysée** : {focus_name} (Score global : {int((focus_city.global_score or 0) * 100)}%)\n"
        f"- **Briefing dossier** : {brief}\n"
    )

    # 2. Local Node Artifacts (Comparator & CCAS)
    city_res = focus_city
    expert_analysis = city_res.expert_analysis if city_res else {}

    comparator_text = expert_analysis.get("city_comparator")
    if comparator_text:
        sections.append(f"{comparator_text.strip()}\n")

    ccas_text = expert_analysis.get("ccas_locator")
    if ccas_text:
        sections.append(f"{ccas_text.strip()}\n")

    # 3. Domain Expert Findings (Compact summaries)
    domain_findings = []
    for domain, content in expert_analysis.items():
        if domain in ("city_comparator", "ccas_locator"):
            continue  # Already included above in dedicated sections

        # Clean markdown / truncate if extremely long
        cleaned_content = content.strip()
        domain_title = domain.replace("_expert", "").replace("_", " ").capitalize()
        domain_findings.append(f"#### 📌 Expert {domain_title} :\n{cleaned_content}\n")

    if domain_findings:
        sections.append("### 🔍 Synthèses des Découvertes Experts :\n" + "\n".join(domain_findings))
    else:
        sections.append("### 🔍 Synthèses des Découvertes Experts :\n*Aucune analyse d'expert disponible.*")

    return "\n---\n".join(sections)


# Instantiate Synthesizer Agent
synthesizer_agent: Agent[ODISDeps, Union[SynthesizerResult, str]] = create_agent(
    "synthesizer",
    deps_type=ODISDeps,
    output_type=Union[SynthesizerResult, str],
)


@synthesizer_agent.system_prompt
async def synth_instructions(ctx: RunContext[ODISDeps]) -> str:
    """Builds the compact Synthesizer prompt using pre-digested artifact snippets."""
    state = ctx.deps.state
    focus_name = state.focus_city.name if state.focus_city else "Non définie"
    current_city_name = "la commune actuelle"
    if state.search_results and state.search_results.current_geo:
        current_city_name = state.search_results.current_geo.name

    composite_context = build_composite_synthesis_context(state)
    last_message = (
        state.messages[-1].get("content", "Non disponible")
        if state.messages
        else "Non disponible"
    )

    mode = state.execution_mode
    prompt_template = (
        SYNTH_SYSTEM_PROMPT_SPECIFIC
        if mode == "specific_ask"
        else SYNTH_SYSTEM_PROMPT_ANALYSIS
    )
    boilerplate = get_swarm_boilerplate("synthesizer")

    prompt = prompt_template.format(
        SWARM_BOILERPLATE=boilerplate,
        COMPOSITE_CONTEXT=composite_context,
        FOCUS_CITY=focus_name,
        CURRENT_CITY_NAME=current_city_name,
        LAST_MESSAGE=last_message,
    )

    logger.info(
        f"💎 [COMPOSITE-SYNTHESIZER] Mode: {mode}. Context length: {len(composite_context)} chars, Full Prompt: {len(prompt)} chars"
    )

    return prompt
