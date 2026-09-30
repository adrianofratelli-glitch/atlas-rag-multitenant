import logging
import os
import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
import contextvars
import voyageai
import telemetry
from resilience import retry_call
from config import CLIENT_ID, DB_NAME
from db import get_client
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("rag_poc.agent")

MODEL = "claude-sonnet-4-6"

ALL_ACCESS = ["publico", "restrito"]
RRF_K = 60  # standard Reciprocal Rank Fusion constant

# Lazy singleton, mirrors db.get_client() — avoids re-instantiating per call.
_voyage = None


VOYAGE_TIMEOUT_S = float(os.getenv("VOYAGE_TIMEOUT_S", "15"))
# Tentativas totais por chamada à Voyage (embed/rerank). 1 = sem retry (comportamento anterior).
VOYAGE_MAX_ATTEMPTS = max(1, int(os.getenv("VOYAGE_MAX_ATTEMPTS", "1")))
VOYAGE_RETRY_BASE_S = float(os.getenv("VOYAGE_RETRY_BASE_S", "0.5"))
# Filtro heurístico de prompt injection nos trechos recuperados (opt-in). Não é controle
# absoluto: o controle real é o filtro metadata.client_id, inescapável nas pipelines.
INJECTION_FILTER = os.getenv("RAG_INJECTION_FILTER", "0") == "1"
# Recusa explícita sem evidência suficiente (opt-in). Score do rerank-2 abaixo do piso = recusa.
# Piso 0.6 escolhido no split `calib` do golden e confirmado no split `test`, que é o avaliado
# (eval/reports/retrieval.md): 0/19 falsas recusas e 3/4 recusas devidas no `test`. O golden é
# sintético, então recalibre com perguntas reais antes de confiar nele em produção.
REFUSE_WEAK_EVIDENCE = os.getenv("RAG_REFUSE_WEAK_EVIDENCE", "0") == "1"
MIN_RERANK_SCORE = float(os.getenv("RAG_MIN_RERANK_SCORE", "0.6"))


def _get_voyage() -> voyageai.Client:
    global _voyage
    if _voyage is None:
        _voyage = voyageai.Client(
            api_key=os.environ["VOYAGE_API_KEY"], timeout=VOYAGE_TIMEOUT_S
        )
    return _voyage


# Cache em memória de embeddings de consulta (pergunta normalizada -> embedding).
# Perguntas iniciais repetidas (starter questions) pulam a chamada à Voyage.
_EMBED_CACHE_MAX = 256
_embed_cache: "OrderedDict[str, list]" = OrderedDict()
_embed_cache_lock = threading.Lock()


def _embed_query(voyage: voyageai.Client, query: str) -> list:
    key = " ".join(query.lower().split())
    with _embed_cache_lock:
        if key in _embed_cache:
            _embed_cache.move_to_end(key)  # LRU
            return _embed_cache[key]
    embedding = retry_call(
        lambda: voyage.embed([query], model="voyage-3", input_type="query").embeddings[0],
        attempts=VOYAGE_MAX_ATTEMPTS, base=VOYAGE_RETRY_BASE_S, label="voyage.embed",
    )
    with _embed_cache_lock:
        _embed_cache[key] = embedding
        _embed_cache.move_to_end(key)
        while len(_embed_cache) > _EMBED_CACHE_MAX:
            _embed_cache.popitem(last=False)
    return embedding


def _vector_pipeline(embedding, top_k, access_levels, sources=None):
    vs = {
        "index": "vector_index",
        "path": "embedding",
        "queryVector": embedding,
        "numCandidates": top_k * 15,
        "limit": top_k,
    }
    # Defense in depth: the tenant already gets its own database, so this is a
    # no-op filter today (one client_id per DB). It becomes a real guarantee
    # instead of a dead field if the topology ever changes to a shared DB.
    conditions = [{"metadata.client_id": CLIENT_ID}]
    if access_levels:
        conditions.append({"metadata.nivel_acesso": {"$in": access_levels}})
    if sources:
        conditions.append({"metadata.source": {"$in": sources}})
    vs["filter"] = conditions[0] if len(conditions) == 1 else {"$and": conditions}
    return [
        {"$vectorSearch": vs},
        {"$project": {"text": 1, "metadata": 1,
                      "vector_score": {"$meta": "vectorSearchScore"}}},
    ]


def _lexical_pipeline(query, top_k, access_levels, sources=None):
    must = [{"text": {"query": query, "path": "text"}}]
    # Defense in depth: see _vector_pipeline — no-op today (one client_id per
    # DB), a real guarantee if the topology ever becomes a shared DB.
    flt = [{"in": {"path": "metadata.client_id", "value": [CLIENT_ID]}}]
    if access_levels:
        flt.append({"in": {"path": "metadata.nivel_acesso", "value": access_levels}})
    if sources:
        flt.append({"in": {"path": "metadata.source", "value": sources}})
    return [
        {"$search": {"index": "text_index", "compound": {"must": must, "filter": flt}}},
        {"$limit": top_k},
        {"$project": {"text": 1, "metadata": 1,
                      "search_score": {"$meta": "searchScore"}}},
    ]


def _drop_injected(chunks: list[dict]) -> tuple[list[dict], int]:
    """Remove trechos com padrão de prompt injection antes de irem ao prompt do Claude."""
    from guardrails import check_injection
    kept = [c for c in chunks if check_injection(c["text"], use_llm=False).ok]
    return kept, len(chunks) - len(kept)


def insufficient_evidence(sources: list[dict], stats: dict) -> bool:
    """True quando não há base para responder: sem trechos, ou (com a flag ligada) melhor
    score do rerank-2 abaixo do piso. Se o rerank degradou, o score não é comparável — não recusa por score."""
    if not stats:  # caminho de histórico da conversa: não depende do corpus
        return False
    if not sources:
        return True
    if REFUSE_WEAK_EVIDENCE and not stats.get("rerank_degraded"):
        return max((s.get("rerank_score") or 0) for s in sources) < MIN_RERANK_SCORE
    return False


def retrieve_context(query: str, top_k: int = 15,
                     access_levels: list | None = None,
                     sources: list | None = None,
                     *, use_lexical: bool = True, use_rerank: bool = True,
                     final_n: int = 8, _capture: list | None = None) -> tuple[str, list[dict], dict]:
    """Hybrid search: vector ∪ lexical retrieval -> RRF -> rerank-2, with an ACL filter.

    access_levels: allowed access levels (e.g. ["publico"]). None means full access.
    sources: restrict retrieval to these `metadata.source` values (the documents
    picked in the UI). None/empty means every indexed document in the tenant DB.
    """
    # Somente frases que referenciam explicitamente a conversa — termos soltos
    # ("sessão", "anterior", "histórico") aparecem em perguntas legítimas sobre
    # o documento e matariam a recuperação.
    history_phrases = [
        "o que eu perguntei",
        "minha pergunta anterior",
        "pergunta que fiz",
        "o que falei antes",
    ]
    if any(p in query.lower() for p in history_phrases):
        return "Responda com base no histórico da conversa.", [], {}

    levels = access_levels if access_levels else ALL_ACCESS
    with telemetry.span("rag.retrieve", client_id=CLIENT_ID, k=top_k, final_n=final_n,
                        use_lexical=use_lexical, use_rerank=use_rerank) as root:
        result = _retrieve(query, top_k, levels, sources, use_lexical, use_rerank, final_n, _capture)
        telemetry.annotate(root, hits=len(result[1]), **{k: v for k, v in result[2].items()
                           if k in ("rerank_degraded", "injection_dropped", "embedding_degraded")})
        return result


def _retrieve(query, top_k, levels, sources, use_lexical, use_rerank, final_n, _capture):
    voyage = _get_voyage()
    collection = get_client()[DB_NAME]["documents"]

    # A slow/unreachable VoyageAI must not take the whole chat turn down with
    # it: fall back to lexical-only retrieval (same degradation already used
    # when the vector index itself errors below) instead of letting a timeout
    # or network error propagate out of retrieve_context.
    with telemetry.span("rag.embed", client_id=CLIENT_ID, model="voyage-3",
                        max_attempts=VOYAGE_MAX_ATTEMPTS) as sp:
        try:
            embedding = _embed_query(voyage, query)
        except Exception:
            logger.exception("query embedding failed (timeout or API error) — falling back to lexical-only")
            embedding = None
            telemetry.annotate(sp, degraded=True)
    vector_pipeline = _vector_pipeline(embedding, top_k, levels, sources) if embedding else None
    lexical_pipeline = _lexical_pipeline(query, top_k, levels, sources) if use_lexical else None

    # 1) Retrieve from both modalities in parallel (two independent aggregations)
    def _run_vector():
        if vector_pipeline is None:
            return []
        with telemetry.span("rag.retrieve.vector", client_id=CLIENT_ID, k=top_k) as sp:
            try:
                rows = list(collection.aggregate(vector_pipeline))
            except Exception:
                logger.exception("vector search failed — falling back to lexical-only")
                return []  # tolerant: if the vector index fails, fall back to lexical only
            telemetry.annotate(sp, hits=len(rows), top_scores=[round(float(r.get("vector_score") or 0), 4) for r in rows[:5]])
            return rows

    def _run_lexical():
        if lexical_pipeline is None:
            return []
        with telemetry.span("rag.retrieve.text", client_id=CLIENT_ID, k=top_k) as sp:
            try:
                rows = list(collection.aggregate(lexical_pipeline))
            except Exception:
                logger.exception("lexical search failed — falling back to vector-only")
                return []  # tolerant: if the lexical index fails, fall back to vector only
            telemetry.annotate(sp, hits=len(rows), top_scores=[round(float(r.get("search_score") or 0), 4) for r in rows[:5]])
            return rows

    with ThreadPoolExecutor(max_workers=2) as pool:
        vector_future = pool.submit(contextvars.copy_context().run, _run_vector)
        lexical_results = _run_lexical()
        vector_results = vector_future.result()

    # 2) Reciprocal Rank Fusion (RRF): merge the two rankings by _id
    fused: dict = {}

    def _fuse(rows, score_key, matched):
        for rank, r in enumerate(rows, start=1):
            key = str(r["_id"])
            entry = fused.setdefault(key, {
                "chunk_id": key,
                "text": r["text"], "metadata": r["metadata"],
                "vector_score": 0.0, "search_score": 0.0,
                "rrf": 0.0, "matched_by": set(),
            })
            entry["rrf"] += 1.0 / (RRF_K + rank)
            entry["matched_by"].add(matched)
            if score_key in r and r[score_key] is not None:
                entry[score_key] = round(float(r[score_key]), 4)

    with telemetry.span("rag.rrf", client_id=CLIENT_ID, rrf_k=RRF_K) as sp:
        _fuse(vector_results, "vector_score", "vetorial")
        _fuse(lexical_results, "search_score", "léxico")
        candidates = sorted(fused.values(), key=lambda x: x["rrf"], reverse=True)
        telemetry.annotate(sp, fused=len(fused), top_rrf=[round(c["rrf"], 5) for c in candidates[:5]])

    if not fused:
        return "Nenhum contexto encontrado.", [], {"mode": "no_context"}

    # 3) rerank-2 (VoyageAI) over the fused set; on failure degrade to the RRF order
    documents = [c["text"] for c in candidates]
    rerank_degraded = False
    with telemetry.span("rag.rerank", client_id=CLIENT_ID, model="rerank-2", candidates=len(documents),
                        max_attempts=VOYAGE_MAX_ATTEMPTS) as sp:
        if not use_rerank:
            top_results = candidates[:final_n]
            for c in top_results:
                c["rerank_score"] = round(c.get("vector_score") or c.get("search_score") or 0, 4)
            telemetry.annotate(sp, skipped=True)
        else:
            try:
                rr = retry_call(
                    lambda: voyage.rerank(query, documents, model="rerank-2", top_k=min(final_n, len(documents))),
                    attempts=VOYAGE_MAX_ATTEMPTS, base=VOYAGE_RETRY_BASE_S, label="voyage.rerank",
                )
                top_results = []
                for item in rr.results:
                    c = candidates[item.index]
                    c["rerank_score"] = round(item.relevance_score, 4)
                    top_results.append(c)
                telemetry.annotate(sp, top_scores=[c["rerank_score"] for c in top_results[:5]])
            except Exception:
                logger.exception("rerank failed — falling back to RRF order")
                rerank_degraded = True
                top_results = candidates[:final_n]
                for c in top_results:
                    c["rerank_score"] = round(c.get("vector_score") or c.get("search_score") or 0, 4)
                telemetry.annotate(sp, degraded=True, fallback="rrf_order")

    injection_dropped = 0
    if INJECTION_FILTER:
        top_results, injection_dropped = _drop_injected(top_results)
        if injection_dropped:
            logger.warning("dropped %d retrieved chunk(s) flagged as prompt injection", injection_dropped)
    if _capture is not None:
        _capture.extend(top_results)
    if not top_results:
        return "Nenhum contexto encontrado.", [], {"mode": "no_context", "injection_dropped": injection_dropped}

    requested_sources = list(sources or [])
    parts = []
    sources = []
    # Dedupe by chunk, not by page: a Markdown corpus has no pagination, so every
    # chunk carries page 0 and a page-keyed set collapsed all eight reranked
    # passages into a single source card.
    seen_chunks: set = set()
    for r in top_results:
        page = r["metadata"].get("page", "?")
        source = r["metadata"].get("source", "")
        chunk_key = r.get("chunk_id") or (source, page, r["text"][:80])
        parts.append(f"[Página {page} | {source}]\n{r['text']}")
        if chunk_key not in seen_chunks:
            sources.append({
                "page": page,
                "source": source,
                "nivel_acesso": r["metadata"].get("nivel_acesso", "publico"),
                "matched_by": sorted(r["matched_by"]),
                "vector_score": r.get("vector_score", 0),
                "rerank_score": r.get("rerank_score", 0),
                "preview": r["text"][:130],
            })
            seen_chunks.add(chunk_key)

    stats = {
        "num_candidates": top_k * 15,        # $vectorSearch numCandidates
        "vector_hits": len(vector_results),  # returned by vector search
        "lexical_hits": len(lexical_results),  # returned by lexical search (Atlas Search)
        "fused": len(fused),                 # unique candidates after RRF
        "reranked": len(top_results),        # after rerank-2
        "rerank_degraded": rerank_degraded,  # rerank-2 failed -> RRF order
        "injection_dropped": injection_dropped,
        "index": "vector_index + text_index",
        "embed_model": "voyage-3",
        "rerank_model": "rerank-2",
        "embed_dim": len(embedding) if embedding else 0,
        "access_levels": levels,
        "sources": requested_sources,
        "hybrid": True,
        "embedding_degraded": embedding is None,
        "query_details": [
            *([{
                "operation": "aggregate / $vectorSearch",
                "namespace": f"{DB_NAME}.documents",
                "pipeline": [
                    {"$vectorSearch": {**vector_pipeline[0]["$vectorSearch"], "queryVector": f"<{len(embedding)} floats omitidos>"}},
                    *vector_pipeline[1:],
                ],
            }] if vector_pipeline else []),
            *([{
                "operation": "aggregate / $search",
                "namespace": f"{DB_NAME}.documents",
                "pipeline": lexical_pipeline,
            }] if lexical_pipeline else []),
        ],
    }

    return "\n\n---\n\n".join(parts), sources, stats
