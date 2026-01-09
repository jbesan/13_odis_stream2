import os
import sys
import logging
from dotenv import load_dotenv
from google import genai

# Add app directory to path
sys.path.append(os.path.abspath('app'))

# Setup logging to see our dumps
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger()

from agents.job_hunter import JobHunterAgent
from agents.state import AgentContext

def repro():
    load_dotenv('app/.env')
    api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("Error: No API key found in app/.env")
        return

    client = genai.Client(api_key=api_key)
    agent = JobHunterAgent(model_id="gemini-2.0-flash-lite-preview-02-05", client=client) # Assuming this model or similar
    
    context = AgentContext()
    # Mocking found_jobs so detail extraction works if needed, 
    # but the Regex in job_hunter.py should catch the ID from message anyway.
    
    # Simulate the failing request
    message = "Peux-tu me donner les détails de l'offre 202GPKJ ?"
    
    print("\n--- STARTING JOB HUNTER RUN ---\n")
    try:
        response = agent.run(message, context)
        print(f"\nFINAL RESPONSE:\n{response}")
    except Exception as e:
        print(f"\nCRASHED WITH ERROR: {e}")

if __name__ == "__main__":
    repro()
