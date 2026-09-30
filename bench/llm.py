"""Shared Anthropic client for the PDF-vs-Markdown benchmark scripts."""
import os
from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()

MODEL = "claude-sonnet-4-6"


def get_client() -> Anthropic:
    return Anthropic(
        api_key="dummy",
        base_url=os.getenv("ANTHROPIC_BASE_URL"),
        default_headers={"api-key": os.environ["ANTHROPIC_API_KEY"]},
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
