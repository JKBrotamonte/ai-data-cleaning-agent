"""One model call with guards: monthly budget check, cost logged to costs.csv, a log line,
and a JSON reply. Every step that asks the model for a plan goes through ask_model()."""

import json
import logging
import sys
from pathlib import Path

from cleaner_agent import costs

ROOT = Path(__file__).resolve().parents[1]
COSTS_FILE = ROOT / "costs.csv"


def extract_json(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise json.JSONDecodeError("no JSON object", text, 0)
    return json.loads(text[start:end + 1])


def reply_text(response) -> str:
    """The reply's text blocks joined (a reply can start with a thinking block)."""
    if response.stop_reason == "refusal":
        sys.exit("The model declined this request. Check the inputs, then rerun.")
    if response.stop_reason == "max_tokens":
        print("Warning: the reply hit max_tokens and was cut off. Raise model.max_tokens in config.yaml.")
    return "".join(block.text for block in response.content if block.type == "text")


def ask_model(cfg: dict, P: dict, system: str, user: str, label: str, raw_path: Path) -> dict:
    """cfg needs agent.name, model, budget.monthly_cap_usd and pricing_usd_per_million_tokens.
    P may hold a costs path; it defaults to costs.csv in the repo root."""
    import anthropic  # imported here so the rest of the package works without an API key
    try:  # ANTHROPIC_API_KEY from a .env file in the repo root (gitignored), if python-dotenv is installed
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env", override=False)
    except ImportError:
        pass

    step = cfg["agent"]["name"]
    log = logging.getLogger(step)
    cost_file = P.get("costs", COSTS_FILE)
    costs.check_budget(cost_file, step, cfg["budget"]["monthly_cap_usd"])
    log.info("START %s model=%s input_chars=%d", label, cfg["model"]["name"], len(user))
    client = anthropic.Anthropic(max_retries=4)
    response = client.messages.create(model=cfg["model"]["name"], max_tokens=cfg["model"]["max_tokens"],
                                      system=system, messages=[{"role": "user", "content": user}])
    price = cfg["pricing_usd_per_million_tokens"]
    usage = response.usage
    cost = (usage.input_tokens * price["input"] + usage.output_tokens * price["output"]) / 1_000_000
    costs.append(cost_file, step, label, usage.input_tokens, usage.output_tokens, cost)
    log.info("API in=%d out=%d cost=$%.4f", usage.input_tokens, usage.output_tokens, cost)
    text = reply_text(response)
    try:
        return extract_json(text)
    except json.JSONDecodeError:
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_text(text, encoding="utf-8")
        log.error("invalid JSON, raw reply saved to %s", raw_path)
        sys.exit(f"Model did not return valid JSON. Raw reply saved to {raw_path}")
