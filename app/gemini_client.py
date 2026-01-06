
import os
import logging
from typing import List, Dict, Any, Optional, Generator, Sequence
from google import genai
from google.genai import types
import json
import time
import googlemaps
import streamlit as st

# Configure Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("gemini_client")

# System Instructions (Social Worker Persona)
from config import (
    WEIGHT_PROFILES, 
    DEFAULT_INC_SERVICES_CORE,
    WALDEC_CORE_INCLUSION,
    CLASSES_SCOLAIRES,
    LOGEMENT_OPTIONS,
    HEBERGEMENT_OPTIONS,
    SANTE_OPTIONS
)
from models import SearchCriterias

import sys
from pathlib import Path

# Ensure project root is in PYTHONPATH for consistent absolute imports
root_path = str(Path(__file__).resolve().parents[1])
if root_path not in sys.path:
    sys.path.insert(0, root_path)

from app.mcp_server import (
    _compute_top_cities_logic, 
    _search_referentiels_logic, 
    _search_commune_logic,
    _search_places_logic, 
    _compute_routes_logic
)


# Format lists for Prompt
WEIGHT_PROFILES_STR = "\n".join([f"- **{k}**: {v}" for k, v in WEIGHT_PROFILES.items()])
CLASSES_SCOLAIRES_STR = ", ".join(CLASSES_SCOLAIRES)
LOGEMENTS_STR = ", ".join(LOGEMENT_OPTIONS)
HEBERGEMENTS_STR = ", ".join(HEBERGEMENT_OPTIONS)
SANTE_STR = ", ".join(SANTE_OPTIONS)

# Load System Instruction from external file
try:
    with open(os.path.join(os.path.dirname(__file__), 'AGENT_PROMPT.md'), 'r') as f:
        prompt_template = f.read()
        
    # Use replace instead of format to avoid conflicts with JSON curly braces in the prompt
    SYSTEM_INSTRUCTION = prompt_template.replace(
        "{WEIGHT_PROFILES_STR}", WEIGHT_PROFILES_STR
    ).replace(
        "{CLASSES_SCOLAIRES_STR}", CLASSES_SCOLAIRES_STR
    ).replace(
        "{LOGEMENTS_STR}", LOGEMENTS_STR
    ).replace(
        "{HEBERGEMENTS_STR}", HEBERGEMENTS_STR
    ).replace(
        "{SANTE_STR}", SANTE_STR
    )
except Exception as e:
    logger.error(f"Failed to load AGENT_PROMPT.md: {e}")
    # Fallback to a minimal prompt or re-raise
    raise e

class OdisAgent:
    def __init__(self, api_key: str, model_id: str = "gemini-2.5-flash"):
        if not api_key:
            raise ValueError("API Key is required for Gemini Client.")
        
        self.client = genai.Client(api_key=api_key)
        self.model_id = model_id
        
        # Tools Configuration
        # We define wrappers to ensure the names match what the Model expects (and what is in the Prompt)
        def compute_top_cities(weight_profile: str, criterias: SearchCriterias) -> Dict[str, Any]:
            """
            Identifies the top recommended cities based on the beneficiary's social/professional needs.
            MUST be called ONLY when all mandatory criterias (commune, distance) are known.
            
            Args:
                weight_profile: [string] Name of the weight profile to use (e.g., 'Famille', 'Équilibré', 'Santé', 'Economique').
                criterias: [SearchCriterias] Structured search criteria containing 'commune_actuelle', 'nb_adultes', etc.

            Returns:
                A dictionary containing:
                - "cities": List of top 10 cities with detailed scores.
                - "criteria_definitions": Dictionary describing the meaning of each criteria score.
            """
            # logger.debug(f"DEBUG: [SDK] compute_top_cities called.")
            # logger.debug(f"DEBUG: weight_profile={weight_profile}")
            # logger.debug(f"DEBUG: criterias={criterias}")
            
            # Resolve weights from profile name
            weights = WEIGHT_PROFILES.get(weight_profile, WEIGHT_PROFILES["Équilibré"])
            # logger.debug(f"DEBUG: Resolved weights={weights}")
            
            # Convert Pydantic model to dict for internal logic if needed, or pass as is if logic handles it
            # mcp_server logic expects a dict for filters
            filters_dict = criterias.model_dump()
            
            try:
                st.toast("🛠️ Calcul des villes...", icon="🛠️")
                return _compute_top_cities_logic(weights, filters_dict)
            except Exception as e:
                logger.error(f"DEBUG: [SDK] compute_top_cities FAILED: {e}")
                raise e

        def search_referentiels(query: str, domain: Optional[str] = None) -> List[Dict[str, Any]]:
            """
            Searches for official French administrative codes.
            
            Args:
                query: The search term (e.g., 'Soudeur', 'Football').
                domain: The target database. MUST be one of: 
                        ['fap_codes' (Jobs), 'formation_codes', 'inclusion_services', 'waldec_codes' (Hobbies), 'regions', 'departements'].
            """
            # logger.debug(f"DEBUG: [SDK] search_referentiels called with query='{query}'")
            st.toast(f"🔎 Recherche référentiel : {query}", icon="🔍")
            return _search_referentiels_logic(query, domain)

        def search_commune(query: str) -> List[Dict[str, Any]]:
            """
            Searches for a French city to get its INSEE code.
            
            Args:
                query: City name provided by the user (e.g. 'Bordeaux').
            """
            logger.info(f"📍 [GEMINI_CLIENT] Calling _search_commune_logic for '{query}'...")
            st.toast(f"📍 Recherche ville : {query}", icon="📍")
            return _search_commune_logic(query)

        def search_places(queries: List[str], location: str) -> Dict[str, Any]:
            """
            Recherche des lieux (POIs), commerces, associations ou services dans un secteur donné.
            Grounding Spatial (Ground 3).
            """
            st.toast(f"🗺️ Recherche de lieux à {location}", icon="🗺️")
            return _search_places_logic(queries, location)

        def compute_routes(origin: str, destination: str, mode: str = "transit") -> Dict[str, Any]:
            """
            Calcule des itinéraires et temps de trajet entre deux points.
            Grounding Spatial (Ground 3) pour valider l'accessibilité.
            """
            st.toast(f"🚗 Itinéraire vers {destination}", icon="🚗")
            return _compute_routes_logic(origin, destination, mode)

        # Phase 1 (Mode A): SELECTION & SPATIAL DATA (All Functions)
        # ODIS + Maps Functions are compatible (Function Calling)
        self.select_tools: List[Any] = [
            compute_top_cities, 
            search_referentiels, 
            search_commune, 
            search_places,  # From mcp_server
            compute_routes  # From mcp_server
        ]
        
        # Phase 2 (Mode B): WEB ANALYSIS (Native Search Only)
        # Strictly isolated to avoid Mixed Mode error (400)
        self.analyse_tools: List[Any] = [
            types.Tool(google_search=types.GoogleSearch())
        ]
        
        self.current_tools: List[Any] = self.select_tools
        self.tool_config = types.ToolConfig(
             function_calling_config=types.FunctionCallingConfig(
                 mode=types.FunctionCallingConfigMode.AUTO
             )
        )
        
        self.chat: Optional[Any] = None

    def start_chat(self, history: Optional[Sequence[Any]] = None, tools: Optional[Sequence[Any]] = None) -> Any:
        """Starts a new chat session with specified tools."""
        target_tools = tools if tools is not None else self.current_tools
        self.chat = self.client.chats.create(
            model=self.model_id,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION,
                tools=list(target_tools) if target_tools is not None else [],
                tool_config=self.tool_config,
                temperature=0.7,
            ),
            history=list(history) if history is not None else []
        )
        return self.chat

    def send_message(self, message: str) -> types.GenerateContentResponse:
        """Sends a message and handles tool calls with phase-aware logic."""
        if not self.chat:
            self.start_chat()

        # Trigger Web Mode ONLY for specific "Soft Data" keywords
        # We exclude generic "recherche" to allow "Recherche un itinéraire" (Maps) to stay in Functions Mode
        is_web_search = any(kw in message.lower() for kw in ["actu", "news", "climat", "vibe", "avis", "vivre", "ambiance", "presse", "article", "web", "google"])
        
        # Determine target toolset
        if is_web_search:
            target_tools = self.analyse_tools
            mode_name = "ANALYSIS (Native Search)"
        else:
            target_tools = self.select_tools
            mode_name = "SELECTION (ODIS/Maps Functions)"

        # Switch tools if necessary
        if self.current_tools != target_tools:
            logger.info(f"🔄 [GEMINI_CLIENT] Switching mode to {mode_name}")
            self.current_tools = target_tools
            # Use _curated_history to preserve context between tool swaps
            history = list(getattr(self.chat, '_curated_history', []))
            self.start_chat(history=history, tools=target_tools)

        try:
            logger.info(f"🚀 [GEMINI_CLIENT] Sending message in {mode_name} mode.")
            assert self.chat is not None
            response = self.chat.send_message(message)
            return response
        except Exception as e:
            logger.error(f"Gemini API Error: {e}")
            raise e

    def send_tool_response(self, function_name: str, result: Any) -> types.GenerateContentResponse:
        """
        Sends the result of a tool execution back to the model.
        """
        
        pass
        
        if not self.chat:
            return types.GenerateContentResponse()
        
        return self.chat.send_message(
            types.Content(
                role="tool",
                parts=[
                    types.Part.from_function_response(
                        name=function_name,
                        response={"result": result}
                    )
                ]
            )
        )

    def execute_tool_local(self, name: str, args: Dict[str, Any]) -> Any:
        """Executes the tool locally (MCP)."""
        # logger.info(f"👉 [GEMINI_CLIENT] Request to execute Tool: {name}")
        # logger.info(f"   Args received: {json.dumps(args, indent=2, default=str)}")
        
        if name == "compute_top_cities":
            try:
                # Handle weight_profile -> weights conversion
                if 'weight_profile' in args:
                    profile = args.pop('weight_profile')
                    args['weights'] = WEIGHT_PROFILES.get(profile, WEIGHT_PROFILES["Équilibré"])
                
                # Handle Pydantic 'criterias' mapping to 'filters' arg of backend logic
                if 'criterias' in args:
                    # Arg is a dict coming from JSON
                    args['filters'] = args.pop('criterias')
                
                # logger.info("   Calling _compute_top_cities_logic...")
                start_time = time.time()
                return _compute_top_cities_logic(**args)
            except Exception as e:
                logger.error(f"❌ [GEMINI_CLIENT] Tool Execution Failed: {e}", exc_info=True)
                raise e
        
        if name == "search_referentiels_logic" or name == "search_referentiels":
             # Note: Gemini might call it by function name '_search_referentiels_logic' or a sanitized version
             # We should handle flexible naming or check how SDK registers it.
             # Usually SDK uses the function name.
             try:
                # logger.info("   Calling _search_referentiels_logic...")
                return _search_referentiels_logic(**args)
             except Exception as e:
                 logger.error(f"❌ [GEMINI_CLIENT] Search Failed: {e}", exc_info=True)
                 raise e

        if name == "search_commune":
             try:
                # logger.info("   Calling _search_commune_logic...")
                return _search_commune_logic(**args)
             except Exception as e:
                 logger.error(f"❌ [GEMINI_CLIENT] Commune Search Failed: {e}", exc_info=True)
                 raise e

        if name == "search_places":
             try:
                 queries = args.get('queries', [])
                 location = args.get('location', '')
                 logger.info(f"🗺️ [GEMINI_CLIENT] Grounding Spatial: search_places '{queries}' in {location}")
                 st.toast(f"🗺️ Recherche de lieux à {location}", icon="🗺️")
                 # Call imported function directly
                 return _search_places_logic(queries, location)
             except Exception as e:
                 logger.error(f"❌ search_places failed: {e}")
                 return {"error": str(e)}



        if name == "compute_routes":
             try:
                 origin = str(args.get('origin', ''))
                 dest = str(args.get('destination', ''))
                 mode = str(args.get('mode', 'transit'))
                 logger.info(f"🚗 [GEMINI_CLIENT] Grounding Spatial: compute_routes from {origin} to {dest}")
                 st.toast(f"🚗 Calcul d'itinéraire vers {dest}", icon="🚗")
                 # Call imported function directly
                 return _compute_routes_logic(origin, dest, mode)
             except Exception as e:
                 logger.error(f"❌ compute_routes failed: {e}")
                 return {"error": str(e)}

        # Custom google_search logic removed. 
        # Native Google Search tools are handled by the SDK.

        error_msg = f"Unknown tool: {name}"
        logger.error(error_msg)
        raise ValueError(error_msg)
