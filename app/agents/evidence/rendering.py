"""Deterministic compatibility rendering for typed expert reports."""

from core.evidence import FinalExpertReport, GapRecord


def render_final_report(report: FinalExpertReport, gaps: list[GapRecord]) -> str:
    analysis = []
    for insight in report.analysis:
        prefix = ""
        if insight.status == "inferred":
            prefix = "*Interprétation —* "
        elif insight.status == "uncertain":
            prefix = "⚠️ *À confirmer —* "
        limitation = f" *Limite : {insight.limitation}*" if insight.limitation else ""
        analysis.append(f"{prefix}{insight.text.strip()}{limitation}")

    parts = ["### Analyse\n\n" + "\n\n".join(analysis)]
    if report.recommended_actions:
        actions = []
        for index, action in enumerate(report.recommended_actions, start=1):
            actions.append(
                f"{index}. **{action.action.strip()}** — {action.rationale.strip()}"
            )
        parts.append("### Pistes d'action\n\n" + "\n".join(actions))
    unresolved = [gap for gap in gaps if gap.status != "resolved"]
    if unresolved:
        items = []
        for gap in unresolved:
            manual = (
                f" Prochaine vérification : {gap.manual_resolution_step}"
                if gap.manual_resolution_step
                else ""
            )
            items.append(
                f"- {gap.question} — **{gap.status}**. "
                f"{gap.impact_if_unresolved}{manual}"
            )
        parts.append("### Points restant à vérifier\n\n" + "\n".join(items))
    return "\n\n".join(part for part in parts if part)
