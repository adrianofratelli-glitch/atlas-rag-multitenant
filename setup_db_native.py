"""Cria coleções e índices do caminho nativo (autoEmbed + text_index) no banco indicado por DB_NAME.

Não toca em nenhum índice existente: só cria o que falta. Grava dado, então passa pela guarda
de banco (`*_test`, ou ALLOW_DEMO_DB_WRITE=1 quando o banco novo é de propósito).
"""
import os
from datetime import timedelta
from pymongo import MongoClient
from config import DB_NAME, NATIVE_EMBED_MODEL, assert_writable_db
from dotenv import load_dotenv

load_dotenv()


def setup() -> None:
    assert_writable_db(DB_NAME)
    client = MongoClient(os.environ["MONGO_URI"])
    db = client[DB_NAME]
    existing = db.list_collection_names()
    for col in ("documents", "conversations"):
        if col not in existing:
            db.create_collection(col)
            print(f"Created collection: {col}")

    retention_days = max(1, int(os.getenv("CONVERSATION_RETENTION_DAYS", "30")))
    db["conversations"].create_index(
        "updated_at", name="updated_at_ttl",
        expireAfterSeconds=int(timedelta(days=retention_days).total_seconds()))
    db["documents"].create_index("metadata.expires_at", name="uploads_ttl", expireAfterSeconds=0)

    docs = db["documents"]
    have = {ix["name"] for ix in docs.list_search_indexes()}
    vector_def = {"fields": [
        {"type": "autoEmbed", "modality": "text", "path": "text", "model": NATIVE_EMBED_MODEL},
        {"type": "filter", "path": "metadata.nivel_acesso"},
        {"type": "filter", "path": "metadata.source"},
        {"type": "filter", "path": "metadata.client_id"},
    ]}
    text_def = {"mappings": {"dynamic": False, "fields": {
        "text": {"type": "string"},
        "metadata": {"type": "document", "fields": {
            "nivel_acesso": {"type": "token"},
            "source": {"type": "token"},
            "client_id": {"type": "token"},
        }},
    }}}
    if "vector_index" not in have:
        docs.create_search_index({"name": "vector_index", "type": "vectorSearch", "definition": vector_def})
        print(f"Created autoEmbed index: vector_index ({NATIVE_EMBED_MODEL})")
    if "text_index" not in have:
        docs.create_search_index({"name": "text_index", "type": "search", "definition": text_def})
        print("Created lexical index: text_index")
    client.close()
    print(f"\nSetup complete for '{DB_NAME}'.")


if __name__ == "__main__":
    setup()
