import pytest
from unittest.mock import MagicMock, patch
from agents.refiner import ContextRefiner
from agents.state import AgentContext

@pytest.fixture
def refiner():
    client = MagicMock()
    # Mock the LLM response
    mock_response = MagicMock()
    mock_response.text = "- Point 1\n- Point 2"
    client.models.generate_content.return_value = mock_response
    return ContextRefiner(model_id="gemini-2.5-flash-lite", client=client)

def test_refiner_briefing_generation(refiner):
    """Verify that the refiner generates a structured briefing."""
    context = AgentContext()
    context.search_criteria = {
        'commune_actuelle': '33063',
        'nb_adultes': 2,
        'weight_profile': 'Famille',
        'codes_metiers': [['D1102'], ['A1203']],
        'codes_formations': [['F123']]
    }
    context.history = [
        {"role": "user", "parts": [{"text": "Je cherche une ville pour ma famille."}]},
        {"role": "model", "parts": [{"text": "D'accord, je vais vous aider."}]}
    ]
    context.focus_city = "Bordeaux"
    context.top_cities = [{'name': 'Bordeaux', 'codgeo': '33063'}]
    
    with patch('agents.refiner._get_labels_for_codes_logic') as mock_labels:
        mock_labels.return_value = {
            '33063': 'Bordeaux',
            'D1102': 'Boulangerie',
            'A1203': 'Agriculture',
            'F123': 'Formation 123'
        }
        briefing = refiner.get_briefing(context)
    
    # Check sections
    assert "### 📋 RÉSUMÉ DU DOSSIER (BRIEFING)" in briefing
    assert "PROJET DE VIE" in briefing
    assert "MÉMOIRE DE L'ÉCHANGE" in briefing
    
    # Check heuristic content
    assert "Localisation : Bordeaux (33063)" in briefing
    assert "Composition : 2 adulte(s)" in briefing
    assert "Priorité : Famille" in briefing
    assert "Métiers (ROME) : Boulangerie (D1102), Agriculture (A1203)" in briefing
    assert "Formations : Formation 123 (F123)" in briefing
    
    # Check LLM content (mocked)
    assert "- Point 1" in briefing
    
    # Check Focus
    assert "**CIBLE ACTUELLE** : Bordeaux (Code INSEE: 33063)" in briefing

def test_refiner_empty_context(refiner):
    """Verify behavior with empty context."""
    context = AgentContext()
    briefing = refiner.get_briefing(context)
    assert "Aucun critère enregistré" in briefing
    assert "Aucun échange préalable" in briefing
