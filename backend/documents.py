"""Document library: upload a file during a demo and put it through the same
ingestion pipeline as the pre-loaded corpus.

The tenant keeps one Atlas database; every document lives in the same
`documents` collection and is told apart by `metadata.source`, which is also a
filter field on both search indexes — so the UI can scope retrieval to the
documents picked there without a second database or a second index build.

Ingestion runs on a single worker thread: VoyageAI's free tier allows 3 requests
per minute, so parallel uploads would only trade one slow job for two stuck ones.
"""
import logging
import os
import re
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

from config import DB_NAME
from db import get_client
from ingest import SUPPORTED_FORMATS, AlreadyIndexedError, ingest

logger = logging.getLogger("rag_poc.documents")

UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "data/uploads"))
# Documents uploaded during a demo are throwaway: they expire on their own so a
# month of demos doesn't leave a month of vectors behind. 0 disables the TTL and
# makes an upload permanent, like a CLI ingestion.
UPLOAD_TTL_HOURS = float(os.getenv("UPLOAD_TTL_HOURS", "24"))
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_MB", "25")) * 1024 * 1024
MAX_SOURCE_LENGTH = 80

# One worker: the embedding provider is rate-limited, so jobs queue instead of
# competing. Kept for the process lifetime (uploads are a demo-time action).
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ingest")

# Bumped whenever the corpus changes, so cached derivations (the document
# outline in backend/api.py) can key off it instead of waiting out a TTL.
corpus_version = 0

_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()
MAX_JOBS_KEPT = 50


class UploadError(ValueError):
    """Rejected before any work is scheduled (bad name, format or size)."""


class ProtectedDocumentError(RuntimeError):
    """The tenant's reference corpus cannot be removed through the app."""


def safe_source_name(filename: str) -> str:
    """Filename -> a stable, filesystem- and Atlas-safe `metadata.source` value."""
    stem = Path(filename or "").stem
    normalized = unicodedata.normalize("NFKD", stem).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", normalized).strip("-._")
    slug = re.sub(r"-{2,}", "-", slug)[:MAX_SOURCE_LENGTH]
    if not slug:
        raise UploadError("nome de arquivo inválido")
    return slug


def validate_extension(filename: str) -> str:
    ext = Path(filename or "").suffix.lower()
    if ext not in SUPPORTED_FORMATS:
        raise UploadError(
            f"formato '{ext or 'desconhecido'}' não suportado. "
            f"Aceitos: {', '.join(sorted(SUPPORTED_FORMATS))}"
        )
    return ext


def validate_size(size: int) -> None:
    if size <= 0:
        raise UploadError("arquivo vazio")
    if size > MAX_UPLOAD_BYTES:
        raise UploadError(
            f"arquivo acima do limite de {MAX_UPLOAD_BYTES // (1024 * 1024)} MB"
        )


DOCUMENTS_LIST_LIMIT = 200


def list_documents() -> dict:
    """Every indexed document in the tenant DB, with its chunk count.

    Returns {"documents": [...], "truncated": bool, "total": int}. Fetches one
    row past the limit to detect truncation without a second `$count` pass;
    `total` is exact (one extra $group is cheap relative to the aggregation
    already run) so the UI can tell the user how many documents exist beyond
    what's shown, not just that some were cut.
    """
    pipeline = [
        {"$group": {
            "_id": "$metadata.source",
            "chunks": {"$sum": 1},
            "file": {"$first": "$metadata.file"},
            # Absent for the pre-loaded corpus — those chunks never expire.
            "expires_at": {"$max": "$metadata.expires_at"},
            "nivel_acesso": {"$addToSet": "$metadata.nivel_acesso"},
        }},
        {"$match": {"_id": {"$ne": None}}},
        {"$facet": {
            "page": [{"$sort": {"_id": 1}}, {"$limit": DOCUMENTS_LIST_LIMIT}],
            "count": [{"$count": "n"}],
        }},
    ]
    result = next(iter(get_client()[DB_NAME]["documents"].aggregate(pipeline)), {})
    rows = result.get("page", [])
    total = (result.get("count") or [{}])[0].get("n", len(rows))
    documents_out = [
        {
            "source": r["_id"],
            "chunks": r["chunks"],
            "file": r.get("file"),
            "expires_at": r["expires_at"].isoformat() if r.get("expires_at") else None,
            "nivel_acesso": sorted(lvl for lvl in (r.get("nivel_acesso") or []) if lvl),
            # Workspace tab that owns this document (see sources_for_scope).
            "workspace": "uploads" if r.get("expires_at") else "base",
        }
        for r in rows
    ]
    return {
        "documents": documents_out,
        "truncated": total > len(documents_out),
        "total": total,
    }


# key: (corpus_version, scope) -> {"ts", "value"}. Same corpus_version key the
# outline cache in backend/api.py uses to invalidate on ingest/delete — a
# `distinct` per chat turn was pure waste since the answer only changes when
# the corpus does.
_scope_cache_lock = threading.Lock()
_scope_cache: dict[tuple, dict] = {}
_SCOPE_CACHE_TTL_S = 3600


def sources_for_scope(scope: str) -> list[str] | None:
    """Document names belonging to one workspace.

    Both workspaces live in the same tenant DB and are told apart by the TTL
    stamp: the CLI-ingested reference corpus carries no `metadata.expires_at`,
    every UI upload carries one. `"all"` returns None — do not scope retrieval.
    Cached by (corpus_version, scope) so a busy chat doesn't re-run `distinct`
    on every turn.
    """
    if scope == "all":
        return None
    key = (corpus_version, scope)
    with _scope_cache_lock:
        entry = _scope_cache.get(key)
        now = time.time()
        if entry and now - entry["ts"] < _SCOPE_CACHE_TTL_S:
            return entry["value"]
    exists = scope == "uploads"
    value = sorted(
        s for s in get_client()[DB_NAME]["documents"].distinct(
            "metadata.source", {"metadata.expires_at": {"$exists": exists}}
        ) if s
    )
    with _scope_cache_lock:
        if len(_scope_cache) >= 16:
            _scope_cache.clear()
        _scope_cache[key] = {"ts": now, "value": value}
    return value


def is_protected(source: str) -> bool:
    """The tenant's reference corpus — ingested by CLI, so it carries no TTL stamp.

    Uploads always get metadata.expires_at; anything without it is the base
    corpus the demo is built on and must never be removable from the app.
    """
    col = get_client()[DB_NAME]["documents"]
    if col.count_documents({"metadata.source": source}, limit=1) == 0:
        return False
    return col.count_documents(
        {"metadata.source": source, "metadata.expires_at": {"$exists": True}}, limit=1
    ) == 0


def delete_document(source: str) -> int:
    if is_protected(source):
        raise ProtectedDocumentError(source)
    result = get_client()[DB_NAME]["documents"].delete_many(
        # Belt and braces: even past the guard, only TTL-stamped (uploaded)
        # chunks can be deleted, so a mixed source can't take the corpus with it.
        {"metadata.source": source, "metadata.expires_at": {"$exists": True}}
    )
    _bump_corpus_version()
    return result.deleted_count


def _bump_corpus_version() -> None:
    global corpus_version
    with _jobs_lock:
        corpus_version += 1


def _snapshot(job: dict) -> dict:
    return {k: v for k, v in job.items() if k != "path"}


def get_job(job_id: str) -> dict | None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        return _snapshot(job) if job else None


def list_jobs() -> list[dict]:
    with _jobs_lock:
        return [_snapshot(j) for j in _jobs.values()]


def _update(job_id: str, **fields) -> None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job:
            job.update(fields)


def _prune_locked() -> None:
    if len(_jobs) <= MAX_JOBS_KEPT:
        return
    finished = [jid for jid, j in _jobs.items() if j["status"] in ("done", "error")]
    for jid in finished[: len(_jobs) - MAX_JOBS_KEPT]:
        _jobs.pop(jid, None)


def start_ingestion(
    filename: str, content: bytes, nivel_acesso: str = "publico", reset: bool = False
) -> dict:
    """Persist the upload and queue it for ingestion. Returns the initial job."""
    validate_extension(filename)
    validate_size(len(content))
    source = safe_source_name(filename)

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    path = UPLOAD_DIR / f"{source}{Path(filename).suffix.lower()}"
    path.write_bytes(content)

    job_id = uuid4().hex
    job = {
        "job_id": job_id,
        "source": source,
        "filename": filename,
        "nivel_acesso": nivel_acesso,
        "status": "queued",
        "phase": "queued",
        "done": 0,
        "total": 0,
        "error": None,
        "ttl_hours": UPLOAD_TTL_HOURS,
        "created_at": time.time(),
        "finished_at": None,
        "path": str(path),
    }
    with _jobs_lock:
        _jobs[job_id] = job
        _prune_locked()

    _executor.submit(_run_job, job_id, str(path), source, nivel_acesso, reset)
    return _snapshot(job)


def _run_job(job_id: str, path: str, source: str, nivel_acesso: str, reset: bool) -> None:
    def on_progress(phase: str, done: int, total: int):
        _update(job_id, phase=phase, done=done, total=total)

    _update(job_id, status="running", phase="loading")
    try:
        result = ingest(
            path,
            reset=reset,
            nivel_acesso=nivel_acesso,
            source_name=source,
            on_progress=on_progress,
            verbose=False,
            ttl_hours=UPLOAD_TTL_HOURS,
        )
        _update(
            job_id,
            status="done",
            phase="done",
            chunks=result["chunks"],
            expires_at=result["expires_at"],
            finished_at=time.time(),
        )
        _bump_corpus_version()
    except AlreadyIndexedError as e:
        _update(
            job_id,
            status="error",
            phase="error",
            error=(
                f"'{source}' já está indexado ({e.existing} chunks). "
                "Reenvie marcando a opção de reindexar."
            ),
            finished_at=time.time(),
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("ingestion job failed job_id=%s source=%s", job_id, source)
        _update(
            job_id,
            status="error",
            phase="error",
            error=f"Falha na ingestão ({type(e).__name__}).",
            finished_at=time.time(),
        )
    finally:
        # The chunks live in Atlas now; keeping the client's file on disk would
        # outlive the TTL that is supposed to make a demo upload disposable.
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:
            logger.warning("could not remove upload %s", path)
