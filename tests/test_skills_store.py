import os
import pytest
from services.knowledge_store import KnowledgeStore

def test_knowledge_store_lifecycle(tmp_path):
    # 1. Initialize in a temp directory
    db_file = tmp_path / "test_knowledge.db"
    store = KnowledgeStore(db_path=str(db_file))
    
    assert db_file.exists()
    
    # 2. Get skill on empty DB should return None
    card = store.get_skill_card("non_existent")
    assert card is None
    
    # 3. Insert a skill card
    store.insert_or_update_skill(
        skill_id="basic_housing_test",
        description="Housing instructions for testing",
        instructions="Look up local rent index and shelters.",
        domain="housing_expert"
    )
    
    # 4. Retrieve single card
    card = store.get_skill_card("basic_housing_test")
    assert card is not None
    assert card["id"] == "basic_housing_test"
    assert card["description"] == "Housing instructions for testing"
    assert card["instructions"] == "Look up local rent index and shelters."
    assert card["domain"] == "housing_expert"
    
    # 5. Retrieve by domain
    housing_cards = store.get_skills_by_domain("housing_expert")
    assert len(housing_cards) == 1
    assert housing_cards[0]["id"] == "basic_housing_test"
    
    empty_cards = store.get_skills_by_domain("mobility_expert")
    assert len(empty_cards) == 0
    
    # 6. Retrieve all
    all_cards = store.get_all_skills()
    assert len(all_cards) == 1
    assert all_cards[0]["id"] == "basic_housing_test"
    
    # 7. Update existing card
    store.insert_or_update_skill(
        skill_id="basic_housing_test",
        description="Updated description",
        instructions="New instructions.",
        domain="housing_expert"
    )
    
    updated_card = store.get_skill_card("basic_housing_test")
    assert updated_card["description"] == "Updated description"
    assert updated_card["instructions"] == "New instructions."
