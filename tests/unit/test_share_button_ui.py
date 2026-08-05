import streamlit as st
from app.ui.results import render_share_search_button


def test_render_share_search_button_enabled_even_when_postscoring_not_done(monkeypatch):
    """Verify render_share_search_button renders an active button even when post-scoring is still running."""
    button_calls = []

    def mock_button(label, **kwargs):
        button_calls.append((label, kwargs))
        return False

    monkeypatch.setattr(st, "button", mock_button)
    monkeypatch.setattr("app.ui.results.st.session_state", {"search_results": True})
    monkeypatch.setattr("app.ui.results.odis_get_bg_result", lambda h: None)

    render_share_search_button(h="hash_123", button_text="Partager")

    assert len(button_calls) == 1
    label, kwargs = button_calls[0]
    assert label == "Partager"
    assert kwargs.get("disabled") is not True


def test_render_share_search_button_enabled_when_postscoring_done(monkeypatch):
    """Verify render_share_search_button renders an active button when post-scoring is complete."""
    button_calls = []

    def mock_button(label, **kwargs):
        button_calls.append((label, kwargs))
        return False

    monkeypatch.setattr(st, "button", mock_button)
    monkeypatch.setattr("app.ui.results.st.session_state", {"search_results": True})
    mock_bg_res = {"pitches": {"69123": "pitch"}, "enrichment": {"69123": {}}}
    monkeypatch.setattr("app.ui.results.odis_get_bg_result", lambda h: mock_bg_res)

    render_share_search_button(h="hash_123", button_text="Partager")

    assert len(button_calls) == 1
    label, kwargs = button_calls[0]
    assert label == "Partager"
    assert kwargs.get("disabled") is not True
