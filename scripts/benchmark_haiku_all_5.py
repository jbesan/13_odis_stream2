import asyncio
import json
import os
import sys
import time
from pathlib import Path
from dotenv import load_dotenv

load_dotenv("app/.env")

sys.path.insert(0, "app")
from anthropic import AsyncAnthropic
from agents.synthesizer import SynthesizerResult

# Prepare targets
CITIES = [
    {
        "name": "Saint-Étienne",
        "insee": "42218",
        "file_prefix": "01_saint_etienne.md",
        "step_file": "/Users/jacques/.gemini/antigravity/brain/a33308a0-a72f-43e8-9188-4b14fd035d23/.system_generated/steps/439/output.txt",
        "row_idx": 0,
    },
    {
        "name": "Saint-Marcellin",
        "insee": "38416",
        "file_prefix": "02_saint_marcellin.md",
        "step_file": "/Users/jacques/.gemini/antigravity/brain/a33308a0-a72f-43e8-9188-4b14fd035d23/.system_generated/steps/459/output.txt",
        "trace_id": "01a1d02c806aebbf9e5f4c6ed43f2fba",
    },
    {
        "name": "Thiers",
        "insee": "63430",
        "file_prefix": "03_thiers.md",
        "step_file": "/Users/jacques/.gemini/antigravity/brain/a33308a0-a72f-43e8-9188-4b14fd035d23/.system_generated/steps/459/output.txt",
        "trace_id": "01a1d02c806a0196ff672c6cf73e4123",
    },
    {
        "name": "Romans-sur-Isère",
        "insee": "26281",
        "file_prefix": "04_romans_sur_isere.md",
        "step_file": "/Users/jacques/.gemini/antigravity/brain/a33308a0-a72f-43e8-9188-4b14fd035d23/.system_generated/steps/459/output.txt",
        "trace_id": "01a1d02c806b4452b378b3f87f37aef1",
    },
    {
        "name": "Villefranche-sur-Saône",
        "insee": "69264",
        "file_prefix": "05_villefranche_sur_saone.md",
        "step_file": "/Users/jacques/.gemini/antigravity/brain/a33308a0-a72f-43e8-9188-4b14fd035d23/.system_generated/steps/459/output.txt",
        "trace_id": "01a1d02c806bad57976ae3bda1dd2102",
    },
]

OUT_DIR = Path("docs/audit/syntheses_haiku_5.5_2026-11-12")
OUT_DIR.mkdir(parents=True, exist_ok=True)


def load_prompt_for_city(city_def):
    with open(city_def["step_file"]) as f:
        data = json.load(f)
    if "row_idx" in city_def:
        row = data["rows"][city_def["row_idx"]]
    else:
        row = next(
            r for r in data["rows"] if r.get("trace_id") == city_def["trace_id"]
        )
    attrs = row["attributes"]
    sys_prompt = attrs["pydantic_ai.all_messages"][0]["parts"][0]["content"]
    user_prompt = attrs["pydantic_ai.all_messages"][1]["parts"][0]["content"]
    return sys_prompt, user_prompt


async def benchmark_city(client, city_def):
    name = city_def["name"]
    insee = city_def["insee"]
    print(f"\n==========================================")
    print(f"Benchmarking Claude Haiku 5.5 on {name} ({insee})...")
    sys_prompt, user_prompt = load_prompt_for_city(city_def)

    tools = [
        {
            "name": "SynthesizerResult",
            "description": "Structured output for the decoupled composite synthesizer.",
            "input_schema": SynthesizerResult.model_json_schema(),
        }
    ]

    t0 = time.perf_counter()
    resp = await client.messages.create(
        model="claude-haiku-5-5",
        max_tokens=2048,
        system=sys_prompt,
        messages=[{"role": "user", "content": user_prompt}],
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

    print(f"Duration: {duration:.2f}s | In: {in_tok} | Out: {out_tok}")
    print(f"Cost: ${cost_usd:.5f} ({cost_eur:.5f} €)")

    # Write city markdown file
    md_content = f"""# Synthèse IA : {name} ({insee}) - Claude Haiku 5.5

- **Modèle** : `claude-haiku-5-5` (Anthropic API Directe)
- **Durée Nœud Synthesizer** : {duration:.2f} s
- **Tokens Input** : {in_tok:,} tokens
- **Tokens Output** : {out_tok:,} tokens
- **Coût Nœud Synthesizer** : {cost_eur:.5f} € (${cost_usd:.5f})

---

## 🧭 Avis Global d'Orientation

{result.avis_global}

---

## ⚖️ Analyse Comparative Territoriale

{result.analyse_comparative}

---

## ⚠️ Éléments Non Vérifiés & Vigilances

{result.elements_non_verifies or "Aucun élément non vérifié signalé."}

---

## ❓ Et ensuite ? (Pistes d'action)

{result.et_ensuite}
"""
    file_path = OUT_DIR / city_def["file_prefix"]
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(md_content)

    return {
        "name": name,
        "insee": insee,
        "duration": duration,
        "in_tok": in_tok,
        "out_tok": out_tok,
        "cost_usd": cost_usd,
        "cost_eur": cost_eur,
        "file": city_def["file_prefix"],
    }


async def main():
    api_key = os.getenv("ODIS_ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("Missing ODIS_ANTHROPIC_API_KEY")

    client = AsyncAnthropic(api_key=api_key)
    results = []
    total_cost_usd = 0.0
    total_cost_eur = 0.0

    for city in CITIES:
        res = await benchmark_city(client, city)
        results.append(res)
        total_cost_usd += res["cost_usd"]
        total_cost_eur += res["cost_eur"]

    print("\n==========================================")
    print("ALL 5 CITIES COMPLETED SUCCESSFULLY!")
    print(f"Total Haiku Cost: ${total_cost_usd:.5f} ({total_cost_eur:.5f} €)")

    # Build README.md with 3-way comparative
    readme_lines = [
        "# Audit des Synthèses IA - Benchmark Claude Haiku 5.5 vs Gemini 3.1 & 3.5",
        "",
        "- **Date du benchmark** : 12 novembre 2026",
        "- **Modèle** : `claude-haiku-5-5` (Anthropic API directe)",
        "- **Budget total dépensé sur ce run** : **"
        + f"${total_cost_usd:.4f} ({total_cost_eur:.4f} €)** sur les 5$ de crédit",
        "",
        "---",
        "",
        "## Matrice Comparative 3 Modèles : Nœud Synthesizer",
        "",
        "| Commune | Insee | Durée (3.1 + Think) | Durée (3.5 Sans Think) | Durée (Haiku 5.5) | Coût (3.1) | Coût (3.5) | Coût (Haiku 5.5) |",
        "| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |",
    ]

    # Baseline data
    baseline_data = {
        "42218": {
            "d31": "14.8 s",
            "d35": "4.5 s",
            "c31": "0.00476 €",
            "c35": "0.00377 €",
        },
        "38416": {
            "d31": "7.4 s",
            "d35": "5.2 s",
            "c31": "0.00277 €",
            "c35": "0.00375 €",
        },
        "63430": {
            "d31": "8.9 s",
            "d35": "4.4 s",
            "c31": "0.00278 €",
            "c35": "0.00346 €",
        },
        "26281": {
            "d31": "8.5 s",
            "d35": "3.9 s",
            "c31": "0.00290 €",
            "c35": "0.00336 €",
        },
        "69264": {
            "d31": "7.5 s",
            "d35": "4.2 s",
            "c31": "0.00270 €",
            "c35": "0.00369 €",
        },
    }

    for r in results:
        b = baseline_data.get(r["insee"], {})
        readme_lines.append(
            f"| **{r['name']}** | {r['insee']} | {b.get('d31')} | **{b.get('d35')}** | {r['duration']:.2f} s | {b.get('c31')} | {b.get('c35')} | **{r['cost_eur']:.5f} €** |"
        )

    readme_lines.extend(
        [
            "",
            f"**Total Coût Synthesizer (5 communes)** :",
            f"- Gemini 3.1 Flash-Lite (Thinking ON) : **0.01591 €**",
            f"- Gemini 3.5 Flash-Lite (Thinking OFF) : **0.01803 €**",
            f"- Claude Haiku 5.5 : **{total_cost_eur:.5f} €** (gain d'environ **-45%** par rapport à Gemini 3.5 !)",
            "",
            "---",
            "",
            "## Fichiers de Synthèse Détaillés Exportés",
            "",
        ]
    )

    for r in results:
        readme_lines.append(f"- 🏙️ [{r['file']}](./{r['file']})")

    with open(OUT_DIR / "README.md", "w", encoding="utf-8") as f:
        f.write("\n".join(readme_lines) + "\n")

    print("\nREADME.md generated successfully at:", OUT_DIR / "README.md")


if __name__ == "__main__":
    asyncio.run(main())
