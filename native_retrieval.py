"""Recuperação híbrida nativa no Atlas: autoEmbed + $rankFusion + $rerank num único aggregation."""
import logging

from config import (
    CLIENT_ID, NATIVE_EMBED_MODEL, NATIVE_RERANK_CANDIDATES, NATIVE_RERANK_MODEL,
)

logger = logging.getLogger("rag_poc.native")

_BRANCH_LABEL = {"vector": "vetorial", "lexical": "léxico"}


def build_native_pipeline(query, top_k, access_levels, sources, final_n,
                          *, use_lexical=True, use_rerank=True):
    # client_id fica obrigatório nos dois ramos (defesa em profundidade, igual ao caminho antigo).
    vs_conditions = [{"metadata.client_id": CLIENT_ID}]
    lex_filter = [{"in": {"path": "metadata.client_id", "value": [CLIENT_ID]}}]
    if access_levels:
        vs_conditions.append({"metadata.nivel_acesso": {"$in": access_levels}})
        lex_filter.append({"in": {"path": "metadata.nivel_acesso", "value": access_levels}})
    if sources:
        vs_conditions.append({"metadata.source": {"$in": sources}})
        lex_filter.append({"in": {"path": "metadata.source", "value": sources}})

    pipelines = {"vector": [{"$vectorSearch": {
        "index": "vector_index", "path": "text", "query": query, "model": NATIVE_EMBED_MODEL,
        "numCandidates": top_k * 15, "limit": top_k,
        "filter": {"$and": vs_conditions},
    }}]}
    if use_lexical:
        pipelines["lexical"] = [
            {"$search": {"index": "text_index", "compound": {
                "must": [{"text": {"query": query, "path": "text"}}], "filter": lex_filter}}},
            {"$limit": top_k},
        ]

    pipeline = [
        {"$rankFusion": {"input": {"pipelines": pipelines}, "scoreDetails": True}},
        {"$set": {"fusion_score": {"$meta": "score"}, "score_details": {"$meta": "scoreDetails"}}},
    ]
    if use_rerank:
        pipeline += [
            # $rerank falha se o campo de `path` não existir em algum documento.
            {"$match": {"text": {"$type": "string", "$ne": ""}}},
            {"$rerank": {"model": NATIVE_RERANK_MODEL, "query": {"text": query}, "path": "text",
                         "numDocsToRerank": max(NATIVE_RERANK_CANDIDATES, final_n)}},
            {"$set": {"rerank_score": {"$meta": "score"}}},
        ]
    pipeline += [
        {"$limit": final_n},
        {"$project": {"text": 1, "metadata": 1, "fusion_score": 1,
                      "score_details": 1, "rerank_score": 1}},
    ]
    return pipeline


def _branches(score_details):
    details = score_details.get("details") if isinstance(score_details, dict) else None
    if not isinstance(details, list):
        return []
    # Ramo em que o chunk não apareceu vem com rank "NA" e value 0 — não conta como acerto.
    return [d for d in details if isinstance(d, dict) and d.get("inputPipelineName") in _BRANCH_LABEL
            and isinstance(d.get("rank"), int)]


def matched_by(score_details):
    return sorted(_BRANCH_LABEL[d["inputPipelineName"]] for d in _branches(score_details))


def pipeline_rank(score_details, name):
    for d in _branches(score_details):
        if d["inputPipelineName"] == name:
            return d.get("rank")
    return None


def pipeline_value(score_details, name):
    """Score bruto do ramo (cosseno no vetorial, BM25 no léxico), antes da fusão."""
    for d in _branches(score_details):
        if d["inputPipelineName"] == name:
            return d.get("value")
    return None


def _lexical_fallback(query, top_k, levels, sources, final_n):
    flt = [{"in": {"path": "metadata.client_id", "value": [CLIENT_ID]}}]
    if levels:
        flt.append({"in": {"path": "metadata.nivel_acesso", "value": levels}})
    if sources:
        flt.append({"in": {"path": "metadata.source", "value": sources}})
    return [
        {"$search": {"index": "text_index", "compound": {
            "must": [{"text": {"query": query, "path": "text"}}], "filter": flt}}},
        {"$limit": final_n},
        {"$project": {"text": 1, "metadata": 1, "search_score": {"$meta": "searchScore"}}},
    ]


def _to_internal(row, *, lexical_only=False):
    rerank = row.get("rerank_score")
    fusion = row.get("fusion_score")
    search = row.get("search_score")
    sd = row.get("score_details")
    return {
        "chunk_id": str(row["_id"]),
        "text": row["text"],
        "metadata": row["metadata"],
        "vector_score": round(float(pipeline_value(sd, "vector") or 0), 4),
        "search_score": round(float(search or pipeline_value(sd, "lexical") or 0), 4),
        "rerank_score": round(float(rerank if rerank is not None else (fusion or search or 0)), 4),
        "matched_by": {"léxico"} if lexical_only else set(matched_by(sd)),
        "vector_rank": pipeline_rank(sd, "vector"),
        "lexical_rank": pipeline_rank(sd, "lexical"),
    }


def _usable(rows, *, lexical_only=False):
    """Descarta linhas sem texto ou metadata (sem `$rerank` nada garante o campo) em vez de falhar o turno."""
    return [_to_internal(r, lexical_only=lexical_only) for r in rows
            if isinstance(r.get("text"), str) and r["text"] and isinstance(r.get("metadata"), dict)]


def run_native(collection, query, top_k, levels, sources, final_n,
               *, use_lexical=True, use_rerank=True):
    """Executa o aggregation nativo. Falha do Atlas degrada para lexical-only; nunca levanta."""
    pipeline = build_native_pipeline(query, top_k, levels, sources, final_n,
                                     use_lexical=use_lexical, use_rerank=use_rerank)
    try:
        rows = list(collection.aggregate(pipeline))
        return _usable(rows), {"degraded": False, "pipeline": pipeline}
    except Exception:
        logger.exception("native pipeline failed — falling back to lexical-only")
    try:
        fallback = _lexical_fallback(query, top_k, levels, sources, final_n)
        rows = list(collection.aggregate(fallback))
        return _usable(rows, lexical_only=True), {"degraded": True, "pipeline": fallback}
    except Exception:
        logger.exception("lexical fallback failed too")
        return [], {"degraded": True, "pipeline": pipeline}
