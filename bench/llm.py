"""Shared Anthropic client for the PDF-vs-Markdown benchmark scripts."""
from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()

MODEL = "claude-sonnet-4-6"


def get_client() -> Anthropic:
    """Same explicit gateway as the app (llm_gateway): never an implicit provider endpoint."""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from llm_gateway import gateway_settings

    gw = gateway_settings()
    return Anthropic(
        api_key=gw["api_key"],
        base_url=gw["base_url"],
        default_headers=gw["headers"],
        timeout=180.0,
        max_retries=3,
    )


def complete(system: str, user: str, max_tokens: int = 8000) -> str:
    resp = get_client().messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        temperature=0,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return "".join(b.text for b in resp.content if b.type == "text")
