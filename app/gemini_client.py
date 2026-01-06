import os
import logging
from typing import List, Dict, Any, Optional, Generator, Sequence
from google import genai
from google.genai import types
import json
import streamlit as st

from agents.orchestrator import MultiAgentOrchestrator
from agents.state import AgentContext

# Configure Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("gemini_client")

class OdisAgent:
    """
    High-level Agent Interface for ODIS.
    Now acts as a wrapper for the Multi-Agent Orchestrator.
    """
    def __init__(self, api_key: str, model_id: str = "gemini-2.5-flash"):
        if not api_key:
            raise ValueError("API Key is required for Gemini Client.")
        
        self.orchestrator = MultiAgentOrchestrator(api_key)
        self.context = AgentContext()
        self.model_id = model_id # Keep for UI compatibility if needed

    def start_chat(self, history: Optional[Sequence[Any]] = None):
        """Initializes/Resets the orchestrator context."""
        self.context = AgentContext()
        if history:
            # Transfer history if provided
            self.context.history = list(history)
        return self

    def send_message(self, message: str) -> Any:
        """
        Sends a message to the orchestrator.
        Returns a response object compatible with the existing UI expectations
        (or at least providing .parts[0].text).
        """
        try:
            logger.info(f"🚀 [ODIS_AGENT] Passing message to Orchestrator...")
            response_text = self.orchestrator.process_message(message, self.context)
            
            # Wrap in a mock response object to satisfy the UI's check for .parts
            class MockPart:
                def __init__(self, text): 
                    self.text = text
                    self.function_call = None
            
            class MockCandidate:
                def __init__(self):
                    self.grounding_metadata = None

            class MockResponse:
                def __init__(self, text, active_agent):
                    self.parts = [MockPart(text)]
                    self.candidates = [MockCandidate()]
                    self.active_agent = active_agent # Custom attribute for UI display
            
            return MockResponse(response_text, self.context.active_agent)
            
        except Exception as e:
            logger.error(f"Orchestrator Error: {e}")
            raise e

    def get_context(self) -> AgentContext:
        """Expose current context for UI debugging."""
        return self.context
