"""Deterministic projection of the social dossier into addressable evidence."""

import json
import re

from pydantic import BaseModel, ConfigDict

from agents.state import GraphState, ODISContextBuilder
from core.evidence import DossierEvidence


class ExpertEvidencePacket(BaseModel):
    model_config = ConfigDict(frozen=True)

    domain: str
    target_city_name: str
    target_codgeo: str
    mission: str
    skill_instructions: str
    dossier_evidence: list[DossierEvidence]

    @property
    def evidence_ids(self) -> set[str]:
        return {record.evidence_id for record in self.dossier_evidence}

    def as_prompt(self) -> str:
        lines = [
            f"Mission: {self.mission}",
            f"Commune cible: {self.target_city_name} ({self.target_codgeo})",
            f"Consignes actives: {self.skill_instructions}",
            "Faits du dossier (citer exclusivement leurs evidence_id):",
        ]
        for item in self.dossier_evidence:
            lines.append(
                f"[{item.evidence_id}] {item.label}: "
                f"{json.dumps(item.value, ensure_ascii=False, separators=(',', ':'))}"
            )
        return "\n".join(lines)


def _slug(value: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return value[:48] or "fact"


def project_social_context(state: GraphState) -> ExpertEvidencePacket:
    raw_context = json.loads(
        ODISContextBuilder.agent_context(state, "social_integration_expert")
    )
    records: list[DossierEvidence] = []
    for index, (label, value) in enumerate(raw_context.items(), start=1):
        records.append(
            DossierEvidence(
                evidence_id=(
                    f"{state.run_id}:social_integration_expert:dossier:"
                    f"{index:02d}-{_slug(label)}"
                ),
                label=label,
                value=value,
            )
        )
    city = state.focus_city
    return ExpertEvidencePacket(
        domain="social_integration_expert",
        target_city_name=city.name if city else "Commune inconnue",
        target_codgeo=city.codgeo if city else "",
        mission=state.expert_tasks.get(
            "social_integration_expert",
            "Analyser l'intégration sociale et le tissu associatif.",
        ),
        skill_instructions=state.expert_skill_instructions.get(
            "social_integration_expert", "Aucune consigne spécifique."
        ),
        dossier_evidence=records,
    )

