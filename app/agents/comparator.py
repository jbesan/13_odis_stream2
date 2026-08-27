"""
Deterministic City Comparator worker (Phase 1).

Compares focus_city indicators against the reference city (current_geo) and optionally
the shortlisted city (commune_pressentie) without LLM calls.
"""

from typing import Optional
from core.models import CommuneResult
from agents.state import AgentArtifact, UsageStats


def compute_city_comparison(
    focus_city: Optional[CommuneResult],
    ref_city: Optional[CommuneResult] = None,
    pressentie_city: Optional[CommuneResult] = None,
) -> AgentArtifact:
    """Computes a deterministic, direction-aware comparison between cities.

    Args:
        focus_city: Target recommendation commune.
        ref_city: Baseline reference commune (e.g. current location).
        pressentie_city: Optional pre-selected/shortlisted commune.

    Returns:
        AgentArtifact with domain='city_comparator' and formatted Markdown table.
    """
    if not focus_city:
        return AgentArtifact(
            domain="city_comparator",
            result="Aucune commune cible spécifiée pour la comparaison.",
            usage=UsageStats(),
        )

    focus_name = focus_city.name
    ref_name = ref_city.name if ref_city else None
    pressentie_name = (
        pressentie_city.name
        if pressentie_city and pressentie_city.codgeo != focus_city.codgeo
        else None
    )

    # 1. No reference city available
    if not ref_city or ref_city.codgeo == focus_city.codgeo:
        # Build a single-city score highlight overview
        lines = [
            f"### 📊 Synthèse des indicateurs clés pour {focus_name}",
            "",
            "| Catégorie | Indicateur clé | Score normé | Valeur observée |",
            "| :--- | :--- | :---: | :---: |",
        ]
        count = 0
        if focus_city.scores:
            for cat, details in focus_city.scores.items():
                for d in details:
                    val_str = (
                        f"{d.valeur_kpi} {d.unit}".strip()
                        if d.valeur_kpi is not None
                        else "N/A"
                    )
                    lines.append(
                        f"| {cat.capitalize()} | {d.label} | {int(d.score_normalise * 100)}% | {val_str} |"
                    )
                    count += 1
                    if count >= 8:
                        break
                if count >= 8:
                    break

        if count == 0:
            lines.append(
                f"| Général | Score global | {int((focus_city.global_score or 0) * 100)}% | Score territorial |"
            )

        return AgentArtifact(
            domain="city_comparator",
            result="\n".join(lines),
            usage=UsageStats(),
        )

    # 2. Comparative Analysis (Focus vs Ref)
    # Collect all indicators across categories
    comparisons = []

    for cat, details_focus in (focus_city.scores or {}).items():
        ref_details_map = {
            d.score_id: d
            for d in (ref_city.scores or {}).get(cat, [])
            if hasattr(d, "score_id")
        }
        press_details_map = (
            {
                d.score_id: d
                for d in (pressentie_city.scores or {}).get(cat, [])
                if hasattr(d, "score_id")
            }
            if pressentie_city
            else {}
        )

        for d_focus in details_focus:
            sid = d_focus.score_id
            d_ref = ref_details_map.get(sid)
            d_press = press_details_map.get(sid) if press_details_map else None

            score_f = d_focus.score_normalise if d_focus.score_normalise is not None else 0.0
            score_r = d_ref.score_normalise if d_ref and d_ref.score_normalise is not None else None

            # Direction-aware delta: normalized score already encodes direction (higher is always better)
            if score_r is not None:
                delta = score_f - score_r
                rel_weight = d_focus.relative_weight if d_focus.relative_weight else 1.0
                weighted_impact = delta * (rel_weight / 100.0)

                val_f_str = (
                    f"{d_focus.valeur_kpi} {d_focus.unit}".strip()
                    if d_focus.valeur_kpi is not None
                    else "N/A"
                )
                val_r_str = (
                    f"{d_ref.valeur_kpi} {d_ref.unit}".strip()
                    if d_ref and d_ref.valeur_kpi is not None
                    else "N/A"
                )
                val_p_str = (
                    f"{d_press.valeur_kpi} {d_press.unit}".strip()
                    if d_press and d_press.valeur_kpi is not None
                    else "-"
                )

                comparisons.append(
                    {
                        "category": cat.capitalize(),
                        "label": d_focus.label,
                        "score_id": sid,
                        "delta": delta,
                        "weighted_impact": weighted_impact,
                        "score_focus": score_f,
                        "score_ref": score_r,
                        "val_focus": val_f_str,
                        "val_ref": val_r_str,
                        "val_press": val_p_str,
                    }
                )

    # Sort to find top advantages and top vigilances
    comparisons.sort(key=lambda x: x["weighted_impact"], reverse=True)
    top_advantages = [c for c in comparisons if c["delta"] > 0][:5]
    top_vigilances = [c for c in sorted(comparisons, key=lambda x: x["weighted_impact"]) if c["delta"] < -0.05][:3]

    lines = [
        f"### ⚖️ Comparatif territorial : {focus_name} vs {ref_name}",
        "",
        f"**Score global** : **{int((focus_city.global_score or 0) * 100)}%** pour {focus_name} "
        f"contre **{int((ref_city.global_score or 0) * 100)}%** pour {ref_name}.",
        "",
    ]

    if top_advantages:
        lines.append("#### 🌟 Principaux atouts comparatifs (en faveur de la commune cible)")
        lines.append("")
        headers = ["Critère", f"{focus_name} (Cible)", f"{ref_name} (Réf)", "Avantage relatif"]
        if pressentie_name:
            headers.insert(3, f"{pressentie_name} (Pressentie)")

        lines.append("| " + " | ".join(headers) + " |")
        lines.append("| " + " | ".join([":---", ":---:", ":---:"] + [":---:"] * (len(headers) - 3)) + " |")

        for item in top_advantages:
            delta_pct = f"+{int(round(item['delta'] * 100))} pts"
            row = [
                f"**{item['label']}** (`{item['category']}`)",
                f"{item['val_focus']} ({int(round(item['score_focus'] * 100))}%)",
                f"{item['val_ref']} ({int(round(item['score_ref'] * 100))}%)",
            ]
            if pressentie_name:
                row.append(item["val_press"])
            row.append(f"**{delta_pct}**")
            lines.append("| " + " | ".join(row) + " |")

        lines.append("")

    if top_vigilances:
        lines.append("#### ⚠️ Points de vigilance comparatifs")
        lines.append("")
        lines.append(f"| Critère | {focus_name} (Cible) | {ref_name} (Réf) | Écart relatif |")
        lines.append("| :--- | :---: | :---: | :---: |")
        for item in top_vigilances:
            delta_pct = f"{int(round(item['delta'] * 100))} pts"
            lines.append(
                f"| **{item['label']}** | {item['val_focus']} ({int(round(item['score_focus'] * 100))}%) | "
                f"{item['val_ref']} ({int(round(item['score_ref'] * 100))}%) | {delta_pct} |"
            )
        lines.append("")

    if not top_advantages and not top_vigilances:
        lines.append("*Les données détaillées des indicateurs ne permettent pas d'établir d'écart chiffré significatif.*")
        lines.append("")

    return AgentArtifact(
        domain="city_comparator",
        result="\n".join(lines),
        usage=UsageStats(),
    )
