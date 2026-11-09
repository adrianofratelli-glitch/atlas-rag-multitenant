"""Shared MongoDB client (singleton).

PyMongo's MongoClient keeps an internal connection pool and is thread-safe.
Creating a client per request discards that pool and adds handshake/TLS
latency to every call, so always reuse this instance.
"""
import logging
import os
from threading import Lock

from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("rag_poc.db")

_client: MongoClient | None = None
_client_lock = Lock()


def get_client() -> MongoClient:
    global _client
    with _client_lock:
        if _client is None:
            _client = MongoClient(os.environ["MONGO_URI"], serverSelectionTimeoutMS=3500)
    return _client


class TenantIdentityMismatch(RuntimeError):
    """The tenant DB's stamped identity doesn't match this process's CLIENT_ID.

    Catches a connection string pointed at the wrong tenant's database — a
    plausible operational mistake (copy-pasted .env, swapped MONGO_URI) that
    would otherwise boot silently and serve/ingest against the wrong tenant.
    """


def verify_tenant_identity(db_name: str, client_id: str) -> None:
    """Write-once, verify-forever tenant identity check.

    On first boot against a given database, stamps a `_meta` document
    recording which CLIENT_ID it belongs to. On every later boot, checks that
    the stamped identity still matches this process's CLIENT_ID and aborts
    with a clear error if it doesn't (e.g. MONGO_URI now points at a
    different tenant's cluster/database while CLIENT_ID stayed the same, or
    vice versa).
    """
    col = get_client()[db_name]["_meta"]
    existing = col.find_one({"_id": "tenant_identity"})
    if existing is None:
        try:
            col.insert_one({"_id": "tenant_identity", "client_id": client_id})
        except DuplicateKeyError:
            # Another process stamped the existing unique _id during startup.
            existing = col.find_one({"_id": "tenant_identity"})
            if existing is None:
                raise
        else:
            logger.info("tenant identity stamped db=%s client_id=%s", db_name, client_id)
            return
    if existing.get("client_id") != client_id:
        raise TenantIdentityMismatch(
            f"Banco '{db_name}' foi identificado anteriormente como tenant "
            f"'{existing.get('client_id')}', mas este processo está configurado "
            f"com CLIENT_ID='{client_id}'. Isso normalmente indica um MONGO_URI "
            "apontando para o banco/tenant errado. Corrija o .env antes de continuar."
        )
