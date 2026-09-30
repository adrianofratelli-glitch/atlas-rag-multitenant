import os
import json
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# CLIENT_ID names the tenant's database (rag_<CLIENT_ID>) and is stamped on
# every chunk (metadata.client_id, see ingest.py) and filtered on every query
# (see agent.py). Copying a .env from another tenant, or simply forgetting to
# set it, must not silently boot against some other database — so there is no
# default. The only way to run without it is to opt in explicitly via
# ALLOW_DEFAULT_TENANT=true (local/dev convenience only).
CLIENT_ID = os.getenv("CLIENT_ID")
if not CLIENT_ID:
    if os.getenv("ALLOW_DEFAULT_TENANT", "").lower() in ("1", "true", "yes"):
        CLIENT_ID = "default"
    else:
        raise RuntimeError(
            "CLIENT_ID não está configurado. Defina CLIENT_ID no .env deste tenant "
            "(nomeia o banco rag_<CLIENT_ID> e é gravado/filtrado em todo chunk). "
            "Para rodar em modo dev sem tenant definido, defina também "
            "ALLOW_DEFAULT_TENANT=true."
        )

CLIENT_NAME = os.getenv("CLIENT_NAME", "Assistente")
DOCUMENT_TITLE = os.getenv("DOCUMENT_TITLE", "Base de Conhecimento")
DOCUMENT_DESCRIPTION = os.getenv("DOCUMENT_DESCRIPTION", "Converse com seus documentos corporativos.")
DB_NAME = os.getenv("DB_NAME", f"rag_{CLIENT_ID}")
SYSTEM_PROMPT_EXTRA = os.getenv("SYSTEM_PROMPT_EXTRA", "")

_cfg_path = Path("client_config.json")
_cfg: dict = {}
if _cfg_path.exists():
    try:
        _cfg = json.loads(_cfg_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise RuntimeError(f"client_config.json is not valid JSON: {e}") from e

QUESTIONS: list[str] = _cfg.get("questions", [
    "📋 O que este documento aborda?",
    "🎯 Quais são os principais objetivos?",
    "📅 Quais são os prazos mais importantes?",
    "💡 Quais são as principais recomendações?",
    "💰 Quais são os custos ou investimentos previstos?",
    "🔐 Quais são os requisitos de segurança?",
    "🏗️ Quais projetos de infraestrutura estão previstos?",
    "📊 Quais são as métricas e indicadores de sucesso?",
])

FOLLOWUPS: dict[str, list[str]] = _cfg.get("followups", {})

DEFAULT_FOLLOWUPS = [
    "📋 Pode detalhar mais sobre este tema?",
    "🗓️ Quais são os próximos passos relacionados?",
]
