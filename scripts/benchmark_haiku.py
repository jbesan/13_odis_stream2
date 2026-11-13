import asyncio
import json
import os
import time
from dotenv import load_dotenv

load_dotenv("app/.env")

from anthropic import AsyncAnthropic
import sys
sys.path.insert(0, "app")

from agents.synthesizer import SynthesizerResult

# Read Saint-Etienne prompt from step 439 output
with open(
    "/Users/jacques/.gemini/antigravity/brain/a33308a0-a72f-43e8-9188-4b14fd035d23/.system_generated/steps/439/output.txt"
) as f:
    data = json.load(f)

attrs = data["rows"][0]["attributes"]
system_prompt = attrs["pydantic_ai.all_messages"][0]["parts"][0]["content"]


async def run():
    api_key = os.getenv("ODIS_ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("Missing ODIS_ANTHROPIC_API_KEY in environment")

    client = AsyncAnthropic(api_key=api_key)
    tools = [
        {
            "name": "SynthesizerResult",
            "description": "Structured output for the decoupled composite synthesizer.",
            "input_schema": SynthesizerResult.model_json_schema(),
        }
    ]

    print("Sending request to Claude Haiku 5.5...")
    t0 = time.perf_counter()
    resp = await client.messages.create(
        model="claude-haiku-5-5",
        max_tokens=2048,
        system=system_prompt,
        messages=[{"role": "user", "content": "Synthèse demandée pour Saint-Étienne."}],
        tools=tools,
        tool_choice={"type": "tool", "name": "SynthesizerResult"},
    )
    t1 = time.perf_counter()
    duration = t1 - t0

    tool_call = next(c for c in resp.content if c.type == "tool_use")
    result = SynthesizerResult.model_validate(tool_call.input)

    in_tok = resp.usage.input_tokens
    out_tok = resp.usage.output_tokens
    cost_usd = (in_tok * 0.10 + out_tok * 0.50) / 1_000_000
    cost_eur = cost_usd * 0.95

    print(f"SUCCESS! Duration: {duration:.2f}s")
    print(f"Tokens: in={in_tok}, out={out_tok}")
    print(f"Cost: ${cost_usd:.5f} ({cost_eur:.5f} €)")
    print("\n--- Avis Global ---")
    print(result.avis_global)
    print("\n--- Analyse Comparative ---")
    print(result.analyse_comparative)
    print("\n--- Elements Non Verifies ---")
    print(result.elements_non_verifies)
    print("\n--- Et Ensuite ---")
    print(result.et_ensuite)


if __name__ == "__main__":
    asyncio.run(run())
