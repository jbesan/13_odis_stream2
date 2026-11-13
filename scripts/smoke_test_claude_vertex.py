"""
Smoke test script for Claude Haiku on Vertex AI (Multi-region EU).

Validates:
1. Direct connection to Vertex AI via AsyncAnthropicVertex.
2. Regional endpoints ('eu', 'europe-west1', etc.).
3. Simple text completion.
4. Structured output via Pydantic AI (SynthesizerResult).
"""

import argparse
import asyncio
import os
import sys
import time
from typing import Optional
from pydantic import BaseModel, Field

# Ensure app is on python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app")))

from anthropic import AsyncAnthropicVertex
from pydantic_ai import Agent
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.providers.anthropic import AnthropicProvider


class SynthesizerResultTest(BaseModel):
    """Pydantic model matching SynthesizerResult for structured output test."""

    avis_global: str = Field(description="Avis global en 2-3 phrases")
    points_forts: list[str] = Field(description="Liste des 2-3 points forts")
    vigilances: list[str] = Field(description="Liste des vigilances")


async def test_vertex_claude(
    project_id: str,
    region: str,
    model_name: str,
) -> None:
    print(f"\n==================================================")
    print(f"Testing Claude on Vertex AI")
    print(f"Project: {project_id}")
    print(f"Region:  {region}")
    print(f"Model:   {model_name}")
    print(f"==================================================")

    # 1. Direct client test
    print("\n[Step 1/2] Direct AsyncAnthropicVertex test...")
    client = AsyncAnthropicVertex(project_id=project_id, region=region)
    start_t = time.perf_counter()
    try:
        msg = await client.messages.create(
            model=model_name,
            max_tokens=100,
            messages=[{"role": "user", "content": "Réponds en 1 phrase courte : Es-tu opérationnel sur Vertex AI EU ?"}],
        )
        duration_step1 = time.perf_counter() - start_t
        print(f"✅ Direct SDK call SUCCESS in {duration_step1:.2f}s")
        print(f"Response: {msg.content[0].text if msg.content else 'Empty'}")
        print(f"Usage: in={msg.usage.input_tokens}, out={msg.usage.output_tokens}")
    except Exception as e:
        duration_step1 = time.perf_counter() - start_t
        print(f"❌ Direct SDK call FAILED in {duration_step1:.2f}s: {e}")
        return

    # 2. Pydantic AI Agent with Structured Output
    print("\n[Step 2/2] Pydantic AI Agent (Structured Output: SynthesizerResultTest)...")
    provider = AnthropicProvider(anthropic_client=client)
    model = AnthropicModel(model_name, provider=provider)
    agent = Agent(model=model, output_type=SynthesizerResultTest)

    start_t = time.perf_counter()
    try:
        res = await agent.run(
            "Analyse brièvement la relocalisation d'une famille monoparentale vers Saint-Étienne."
        )
        duration_step2 = time.perf_counter() - start_t
        print(f"✅ Pydantic AI structured call SUCCESS in {duration_step2:.2f}s")
        print(f"Result type: {type(res.output).__name__}")
        print(f"Avis Global: {res.output.avis_global}")
        print(f"Points Forts: {res.output.points_forts}")
        print(f"Vigilances: {res.output.vigilances}")
        usage = res.usage() if callable(res.usage) else res.usage
        if usage:
            print(f"Usage: in={usage.input_tokens}, out={usage.output_tokens}")
    except Exception as e:
        duration_step2 = time.perf_counter() - start_t
        print(f"❌ Pydantic AI structured call FAILED in {duration_step2:.2f}s: {e}")


def main():
    parser = argparse.ArgumentParser(description="Test Claude on Vertex AI")
    parser.add_argument("--project", default=os.getenv("ODIS_AGENT_GCP_PROJECT", "odis-stream2-app"))
    parser.add_argument("--region", default="eu")
    parser.add_argument("--model", default="claude-haiku-5-5")
    args = parser.parse_args()

    asyncio.run(test_vertex_claude(args.project, args.region, args.model))


if __name__ == "__main__":
    main()
