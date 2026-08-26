"""
Deterministic CCAS Locator worker (Phase 1).

Queries CCAS datasets for target communes and handles Bassin de Vie fallbacks
without LLM calls.
"""

from typing import Optional, List, Dict, Any
import logging
from core.models import CommuneResult
from agents.state import AgentArtifact, UsageStats
from agents.tools import search_ccas

logger = logging.getLogger("ccas_worker")


def locate_ccas_deterministic(focus_city: Optional[CommuneResult]) -> AgentArtifact:
    """Deterministically locates and formats CCAS records for the focus city.

    If no CCAS is registered in the specific commune, expands to the Bassin de Vie
    and returns a clear proximity table.

    Args:
        focus_city: Target recommendation commune.

    Returns:
        AgentArtifact with domain='ccas_locator' and Markdown contact card/table.
    """
    if not focus_city or not focus_city.codgeo:
        return AgentArtifact(
            domain="ccas_locator",
            result="Aucune commune spécifiée pour la recherche CCAS.",
            usage=UsageStats(),
        )

    codgeo = str(focus_city.codgeo)
    focus_name = focus_city.name

    try:
        records: List[Dict[str, Any]] = search_ccas(codgeo)
    except Exception as e:
        logger.warning(f"⚠️ [CCAS-LOCATOR] Error querying CCAS for {codgeo}: {e}")
        return AgentArtifact(
            domain="ccas_locator",
            result=f"# 🏛️ Contact du CCAS de {focus_name}\n\n*Information CCAS temporairement indisponible.*",
            usage=UsageStats(),
        )

    if not records:
        return AgentArtifact(
            domain="ccas_locator",
            result=(
                f"# 🏛️ Contact du CCAS de {focus_name}\n\n"
                f"*Aucun CCAS n'est référencé pour la ville visée ni dans son bassin de vie immédiat.*"
            ),
            usage=UsageStats(),
        )

    # Distinguish direct commune matches from Bassin de Vie fallback matches
    direct_matches = [r for r in records if str(r.get("codgeo", "")) == codgeo]
    bv_matches = [r for r in records if str(r.get("codgeo", "")) != codgeo]

    lines = [f"# 🏛️ Contact du CCAS de {focus_name}", ""]

    if direct_matches:
        for r in direct_matches:
            nom = r.get("nom") or r.get("nom_structure") or f"CCAS de {focus_name}"
            adresse = r.get("adresse") or r.get("adresse_complete") or "Adresse non renseignée"
            cp = r.get("code_postal") or ""
            ville = r.get("commune") or focus_name
            tel = r.get("telephone") or r.get("tel") or "Non renseigné"
            email = r.get("courriel") or r.get("email") or "Non renseigné"
            site = r.get("site_web") or r.get("url")

            lines.append(f"**{nom}** (CCAS communal)")
            lines.append(f"- 📍 **Adresse** : {adresse}, {cp} {ville}".strip(", "))
            lines.append(f"- 📞 **Téléphone** : `{tel}`")
            lines.append(f"- ✉️ **Courriel** : `{email}`")
            if site and str(site).startswith("http"):
                lines.append(f"- 🌐 **Site web** : [{site}]({site})")
            lines.append("")
    else:
        # Product input: When no local CCAS, clarify expansion to Bassin de Vie
        lines.append(
            "*Aucun CCAS n'est référencé pour la ville visée, "
            "cependant plusieurs villes à proximité proposent un CCAS :*"
        )
        lines.append("")
        lines.append("| Structure | Commune | Téléphone | Courriel |")
        lines.append("| :--- | :--- | :---: | :---: |")

        for r in bv_matches[:6]:  # Show top 6 nearby CCAS
            nom = r.get("nom") or r.get("nom_structure") or "CCAS Proximité"
            ville = r.get("commune") or "Commune voisine"
            cp = r.get("code_postal") or ""
            tel = r.get("telephone") or r.get("tel") or "-"
            email = r.get("courriel") or r.get("email") or "-"
            loc_str = f"{ville} ({cp})".strip() if cp else ville

            lines.append(f"| **{nom}** | {loc_str} | `{tel}` | `{email}` |")

        lines.append("")

    return AgentArtifact(
        domain="ccas_locator",
        result="\n".join(lines).strip(),
        usage=UsageStats(),
    )
