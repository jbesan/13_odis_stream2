from unittest.mock import patch
from core.models import CommuneResult
from agents.ccas_worker import locate_ccas_deterministic
from agents.state import AgentArtifact


def test_ccas_locator_direct_match():
    """Verify formatting when a direct CCAS match is found in the focus commune."""
    focus = CommuneResult(codgeo="33063", name="Bordeaux")

    mock_records = [
        {
            "nom": "CCAS de Bordeaux",
            "codgeo": "33063",
            "commune": "Bordeaux",
            "adresse": "12 rue Père Louis de Jabrun",
            "code_postal": "33000",
            "telephone": "05 56 10 20 30",
            "courriel": "contact@ccas-bordeaux.fr",
            "site_web": "https://www.bordeaux.fr",
        }
    ]

    with patch("agents.ccas_worker.search_ccas", return_value=mock_records):
        artifact = locate_ccas_deterministic(focus)
        assert isinstance(artifact, AgentArtifact)
        assert artifact.domain == "ccas_locator"
        assert artifact.usage.total_tokens == 0

        res = artifact.result
        assert "Contact du CCAS de Bordeaux" in res
        assert "CCAS de Bordeaux" in res
        assert "12 rue Père Louis de Jabrun" in res
        assert "05 56 10 20 30" in res
        assert "contact@ccas-bordeaux.fr" in res
        assert "https://www.bordeaux.fr" in res


def test_ccas_locator_bassin_de_vie_fallback():
    """Verify formatting when no CCAS exists in the commune and search falls back to Bassin de Vie."""
    focus = CommuneResult(codgeo="33067", name="Bouliac")

    # Mock records belong to neighboring communes in the same Bassin de Vie (not 33067)
    mock_records = [
        {
            "nom": "CCAS de Cenon",
            "codgeo": "33119",
            "commune": "Cenon",
            "code_postal": "33150",
            "telephone": "05 57 80 97 00",
            "courriel": "ccas@cenon.fr",
        },
        {
            "nom": "CCAS de Floirac",
            "codgeo": "33167",
            "commune": "Floirac",
            "code_postal": "33270",
            "telephone": "05 57 80 87 00",
            "courriel": "ccas@floirac.fr",
        },
    ]

    with patch("agents.ccas_worker.search_ccas", return_value=mock_records):
        artifact = locate_ccas_deterministic(focus)
        assert artifact.domain == "ccas_locator"
        assert artifact.usage.total_tokens == 0

        res = artifact.result
        assert "Contact du CCAS de Bouliac" in res
        # User requirement: Specific fallback notice
        assert "Aucun CCAS n'est référencé pour la ville visée, cependant plusieurs villes à proximité proposent un CCAS" in res
        assert "CCAS de Cenon" in res
        assert "Cenon (33150)" in res
        assert "CCAS de Floirac" in res
        assert "Floirac (33270)" in res


def test_ccas_locator_empty_results():
    """Verify formatting when no CCAS is found at all."""
    focus = CommuneResult(codgeo="99999", name="Commune Inconnue")

    with patch("agents.ccas_worker.search_ccas", return_value=[]):
        artifact = locate_ccas_deterministic(focus)
        assert artifact.domain == "ccas_locator"
        assert "Aucun CCAS n'est référencé pour la ville visée ni dans son bassin de vie immédiat" in artifact.result


def test_ccas_locator_exception_handling():
    """Verify graceful handling when an exception occurs during lookup."""
    focus = CommuneResult(codgeo="33063", name="Bordeaux")

    with patch("agents.ccas_worker.search_ccas", side_effect=RuntimeError("Data source unavailable")):
        artifact = locate_ccas_deterministic(focus)
        assert artifact.domain == "ccas_locator"
        assert "Information CCAS temporairement indisponible" in artifact.result
