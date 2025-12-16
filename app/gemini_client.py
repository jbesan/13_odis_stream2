
import os
import logging
from typing import List, Dict, Any, Optional, Generator
from google import genai
from google.genai import types
from mcp_server import _compute_top_cities_logic
import json

# Configure Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("gemini_client")

# System Instructions (Social Worker Persona)
from config import (
    LOC_DISTANCE_OPTIONS, 
    WEIGHT_PROFILES, 
    DEFAULT_SOCLE_ADMIN,
    WALDEC_INTERESTS_MAPPING,
    CLASSES_SCOLAIRES
)
from models import SearchCriterias

# Ensure implicit imports work
try:
    from app.mcp_server import _compute_top_cities_logic, _search_referentiels_logic, _search_commune_logic
    logger.info("Successfully imported tools from app.mcp_server")
except ImportError:
    try:
        from mcp_server import _compute_top_cities_logic, _search_referentiels_logic, _search_commune_logic
        logger.info("Successfully imported tools from mcp_server")
    except ImportError as e:
         logger.error(f"CRITICAL: Could not import mcp_server: {e}")
         # Dummies
        #  def _compute_top_cities_logic(*args, **kwargs): raise ImportError("mcp_server error")
        #  def _search_referentiels_logic(*args, **kwargs): raise ImportError("mcp_server error")
        #  def _search_commune_logic(*args, **kwargs): raise ImportError("mcp_server error")

# Format lists for Prompt
WEIGHT_PROFILES_STR = "\n".join([f"- **{k}**: {v}" for k, v in WEIGHT_PROFILES.items()])
CLASSES_SCOLAIRES_STR = ", ".join(CLASSES_SCOLAIRES)
DEFAULT_SOCLE_ADMIN_STR = ", ".join(DEFAULT_SOCLE_ADMIN)

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
        "{DEFAULT_SOCLE_ADMIN_STR}", DEFAULT_SOCLE_ADMIN_STR
    )
except Exception as e:
    logger.error(f"Failed to load AGENT_PROMPT.md: {e}")
    # Fallback to a minimal prompt or re-raise
    raise e

class OdisAgent:
    def __init__(self, api_key: str):
        if not api_key:
            raise ValueError("API Key is required for Gemini Client.")
        
        self.client = genai.Client(api_key=api_key)
        # User requested 2.5-flash-lite
        self.client = genai.Client(api_key=api_key)
        # Upgrade to 2.5 Flash for better Tool Use
        self.model_id = "gemini-2.5-flash-lite"
        
        # Tools Configuration
        # We define wrappers to ensure the names match what the Model expects (and what is in the Prompt)
        def compute_topcities(weight_profile: str, criterias: SearchCriterias) -> List[Dict[str, Any]]:
            """
            Identifies the top recommended cities based on the beneficiary's social/professional needs.
            MUST be called ONLY when all mandatory criterias (commune, distance) are known.
            
            Args:
                weight_profile: Name of the weight profile to use (e.g., 'Famille', 'Équilibré', 'Santé', 'Economique').
                criterias: Structured search criteria containing 'commune_actuelle', 'nb_adultes', etc.
            """
            logger.debug(f"DEBUG: [SDK] compute_topcities called.")
            logger.debug(f"DEBUG: weight_profile={weight_profile}")
            logger.debug(f"DEBUG: criterias={criterias}")
            
            # Resolve weights from profile name
            weights = WEIGHT_PROFILES.get(weight_profile, WEIGHT_PROFILES["Équilibré"])
            logger.debug(f"DEBUG: Resolved weights={weights}")
            
            # Convert Pydantic model to dict for internal logic if needed, or pass as is if logic handles it
            # mcp_server logic expects a dict for filters
            filters_dict = criterias.model_dump()
            
            try:
                return _compute_top_cities_logic(weights, filters_dict)
            except Exception as e:
                logger.error(f"DEBUG: [SDK] compute_topcities FAILED: {e}")
                raise e

        def search_referentiels(query: str, domain: str = None) -> List[Dict[str, str]]:
            """
            Searches for official French administrative codes.
            
            Args:
                query: The search term (e.g., 'Soudeur', 'Football').
                domain: The target database. MUST be one of: 
                        ['fap_codes' (Jobs), 'formation_codes', 'inclusion_services', 'waldec_codes' (Hobbies)].
            """
            logger.debug(f"DEBUG: [SDK] search_referentiels called with query='{query}'")
            return _search_referentiels_logic(query, domain)

        def search_commune(query: str) -> List[Dict[str, str]]:
            """
            Searches for a French city to get its INSEE code.
            
            Args:
                query: City name provided by the user (e.g. 'Bordeaux').
            """
            logger.debug(f"DEBUG: [SDK] search_commune called with query='{query}'")
            return _search_commune_logic(query)

        self.tools = [compute_topcities, search_referentiels, search_commune]
        self.tool_config = types.ToolConfig(
             function_calling_config=types.FunctionCallingConfig(
                 mode='AUTO' 
             )
        )
        
        self.chat = None

    def start_chat(self, history: List[types.Content] = None):
        """Starts a new chat session."""
        self.chat = self.client.chats.create(
            model=self.model_id,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION,
                tools=self.tools,
                tool_config=self.tool_config,
                temperature=0.7,
            ),
            history=history or []
        )
        return self.chat

    def send_message(self, message: str) -> types.GenerateContentResponse:
        """Sends a message to the agent."""
        if not self.chat:
            self.start_chat()
            
        try:
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
        logger.info(f"👉 [GEMINI_CLIENT] Request to execute Tool: {name}")
        logger.info(f"   Args received: {json.dumps(args, indent=2, default=str)}")
        
        if name == "compute_topcities":
            try:
                # Handle weight_profile -> weights conversion
                if 'weight_profile' in args:
                    profile = args.pop('weight_profile')
                    args['weights'] = WEIGHT_PROFILES.get(profile, WEIGHT_PROFILES["Équilibré"])
                
                # Handle Pydantic 'criterias' mapping to 'filters' arg of backend logic
                if 'criterias' in args:
                    # Arg is a dict coming from JSON
                    args['filters'] = args.pop('criterias')
                
                logger.info("   Calling _compute_top_cities_logic...")
                start_time = time.time()
                result = _compute_top_cities_logic(**args)
                duration = time.time() - start_time
                logger.info(f"✅ [GEMINI_CLIENT] Tool Execution Success ({duration:.2f}s). Result count: {len(result)}")
                return result
            except Exception as e:
                logger.error(f"❌ [GEMINI_CLIENT] Tool Execution Failed: {e}", exc_info=True)
                raise e
        
        if name == "search_referentiels_logic" or name == "search_referentiels":
             # Note: Gemini might call it by function name '_search_referentiels_logic' or a sanitized version
             # We should handle flexible naming or check how SDK registers it.
             # Usually SDK uses the function name.
             try:
                logger.info("   Calling _search_referentiels_logic...")
                result = _search_referentiels_logic(**args)
                logger.info(f"✅ [GEMINI_CLIENT] Search Success. Result count: {len(result)}")
                return result
             except Exception as e:
                 logger.error(f"❌ [GEMINI_CLIENT] Search Failed: {e}", exc_info=True)
                 raise e

        if name == "search_commune":
             try:
                logger.info("   Calling _search_commune_logic...")
                result = _search_commune_logic(**args)
                logger.info(f"✅ [GEMINI_CLIENT] Commune Search Success. Result count: {len(result)}")
                return result
             except Exception as e:
                 logger.error(f"❌ [GEMINI_CLIENT] Commune Search Failed: {e}", exc_info=True)
                 raise e

        error_msg = f"Unknown tool: {name}"
        logger.error(error_msg)
        raise ValueError(error_msg)
