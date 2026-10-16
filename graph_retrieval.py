"""Graph-RAG no Atlas: entrada semântica + travessia do grafo + evidência reranqueada, num aggregation só.

    kg_entities.aggregate([
      $rankFusion { $vectorSearch (autoEmbed nas entidades) + $search (nome, sigla, código) }  # pontos de entrada
      $limit seeds
      $graphLookup links -> _id, maxDepth hops-1                                             # vizinhança
      $group                                                                                 # um subgrafo
      $lookup documents { $match chunks citados pelas entidades/arestas + $rerank }          # evidência
      $unionWith documents { $rankFusion + $rerank }                                        # trechos diretos
      $unionWith kg_communities { $vectorSearch }                                            # sumários globais
    ])

Os trechos diretos (busca híbrida comum, poucos) entram junto com os citados pelo grafo e os dois
conjuntos disputam as vagas finais pelo score do mesmo `$rerank`: só com os trechos do subgrafo,
um fato pontual que o grafo não modelou ("qual o prazo da ação X") virava "não encontrei".

O grafo (`kg_entities`, `kg_communities`) é construído por `build_graph.py` no mesmo banco dos
chunks. Ao modelo vão três blocos: fatos do grafo (triplas com fonte), sumários de comunidade e
os trechos originais reranqueados; só os trechos são citados como fonte.
"""
import logging
import os
import re

from config import CLIENT_ID, NATIVE_EMBED_MODEL, NATIVE_RERANK_MODEL

logger = logging.getLogger("rag_poc.graph")

SEEDS = int(os.getenv("GRAPH_SEEDS", "6"))
SEED_BRANCH_K = int(os.getenv("GRAPH_SEED_BRANCH_K", "10"))
HOPS = max(1, min(3, int(os.getenv("GRAPH_HOPS", "2"))))
# Nó com mais vizinhos que isso (um objetivo estratégico ligado a cem ações, um indicador
# genérico) aparece no subgrafo, mas a travessia não atravessa por ele: sem esse corte, dois
# saltos a partir de qualquer ação chegam a quase o grafo inteiro.
HUB_DEGREE = int(os.getenv("GRAPH_HUB_DEGREE", "40"))
NEIGHBORS_PER_SEED = int(os.getenv("GRAPH_NEIGHBORS_PER_SEED", "60"))
EVIDENCE_CANDIDATES = int(os.getenv("GRAPH_EVIDENCE_CANDIDATES", "60"))
COMMUNITIES_K = int(os.getenv("GRAPH_COMMUNITIES_K", "2"))
DIRECT_CHUNKS = int(os.getenv("GRAPH_DIRECT_CHUNKS", "4"))
_CODE_RX = re.compile(r"\b([A-Za-z]{2,5})[\s-]?(\d{1,3})\b")


def query_codes(query):
    """Códigos citados na pergunta ("AG56", "ON 02", "okr-1") na forma gravada em `code`."""
    return sorted({f"{a.upper()}{b}" for a, b in _CODE_RX.findall(query)}
                  | {f"{a.upper()}-{b}" for a, b in _CODE_RX.findall(query)})
MAX_FACTS = int(os.getenv("GRAPH_MAX_FACTS", "120"))
MAX_ENTITY_LINES = int(os.getenv("GRAPH_MAX_ENTITY_LINES", "60"))
MAX_DRAW_NODES = 40

ENTITY_FIELDS = {"name": 1, "type": 1, "code": 1, "description": 1, "sources": 1, "degree": 1,
                 "community": 1, "chunk_ids": 1, "relations": 1}
_BRANCH_LABEL = {"vector": "vetorial", "lexical": "léxico"}


def _ids_of(array_expr, field, per_item):
    """Concatena os primeiros `per_item` valores de `field` de cada item de um array."""
    return {"$reduce": {"input": array_expr, "initialValue": [], "in": {
        "$concatArrays": ["$$value", {"$slice": [{"$ifNull": [f"$$this.{field}", []]}, per_item]}]}}}


def build_graph_pipeline(query, *, seeds=SEEDS, hops=HOPS, final_n=8, rerank=True,
                         communities_k=COMMUNITIES_K, direct_k=DIRECT_CHUNKS,
                         access_levels=None, sources=None, scope="base"):
    # `scope` separa o grafo do corpus base ("base") do grafo dos uploads ("uploads"), pela
    # mesma regra das abas: tenant + escopo em todo estágio que lê entidade ou chunk.
    uploads = scope == "uploads"
    vs_filter = {"client_id": CLIENT_ID, "scope": scope}
    lex_filter = [{"equals": {"path": "client_id", "value": CLIENT_ID}},
                  {"equals": {"path": "scope", "value": scope}}]
    if uploads and sources:
        vs_filter["sources"] = {"$in": list(sources)}
        lex_filter.append({"in": {"path": "sources", "value": list(sources)}})
    if uploads:
        communities_k = 0  # comunidades só existem para o corpus base
    entry = {"$rankFusion": {"input": {"pipelines": {
        "vector": [{"$vectorSearch": {
            "index": "kg_vector_index", "path": "text", "query": query, "model": NATIVE_EMBED_MODEL,
            "numCandidates": SEED_BRANCH_K * 15, "limit": SEED_BRANCH_K, "filter": vs_filter}}],
        "lexical": [
            {"$search": {"index": "kg_text_index", "compound": {
                "should": [
                    {"text": {"query": query, "path": "name", "score": {"boost": {"value": 3}}}},
                    {"text": {"query": query, "path": "aliases", "score": {"boost": {"value": 2}}}},
                    {"text": {"query": query, "path": "description"}},
                    {"text": {"query": query, "path": "name", "fuzzy": {"maxEdits": 1, "prefixLength": 2}}},
                    *([{"in": {"path": "code", "value": query_codes(query), "score": {"constant": {"value": 100}}}}]
                      if query_codes(query) else []),
                ],
                "minimumShouldMatch": 1, "filter": lex_filter}}},
            {"$limit": SEED_BRANCH_K},
        ],
    }}, "scoreDetails": True}}

    compact = {"_id": 1, "name": 1, "type": 1, "code": 1, "degree": 1, "sources": 1, "description": 1,
               "links": 1, "chunk_ids": {"$slice": ["$chunk_ids", 2]},
               "relations": {"$map": {"input": "$relations", "as": "r", "in": {
                   "target": "$$r.target", "type": "$$r.type", "sources": "$$r.sources",
                   "chunk_ids": {"$slice": ["$$r.chunk_ids", 2]}}}}}
    tagged = lambda arr, hop: {"$map": {"input": arr, "as": "n", "in": {"$mergeObjects": ["$$n", {"hop": hop}]}}}  # noqa: E731
    neighbor_view = {"$map": {
        "input": {"$slice": [{"$sortArray": {
            "input": {"$concatArrays": [
                tagged("$hop1", 1),
                tagged("$hop2", {"$add": ["$$n.hop", 2]}) if hops > 1 else [],
            ]},
            "sortBy": {"hop": 1, "degree": -1, "_id": 1}}}, NEIGHBORS_PER_SEED]},
        "as": "n", "in": {k: f"$$n.{k}" for k in (
            "_id", "name", "type", "code", "hop", "degree", "sources", "description", "chunk_ids", "relations")},
    }}

    seed_chunks = _ids_of("$seeds", "chunk_ids", 4)
    edge_chunks = {"$reduce": {"input": "$seeds", "initialValue": [], "in": {
        "$concatArrays": ["$$value", _ids_of({"$slice": ["$$this.relations", 12]}, "chunk_ids", 2)]}}}
    neighbor_chunks = {"$reduce": {"input": "$seeds", "initialValue": [], "in": {
        "$concatArrays": ["$$value", _ids_of(
            {"$filter": {"input": "$$this.neighbors", "as": "n", "cond": {"$eq": ["$$n.hop", 1]}}},
            "chunk_ids", 1)]}}}

    evidence_match = {"$expr": {"$in": ["$_id", "$$ids"]}, "metadata.client_id": CLIENT_ID,
                      "metadata.expires_at": {"$exists": uploads}}
    if uploads and sources:
        evidence_match["metadata.source"] = {"$in": list(sources)}
    evidence_pipeline = [
        {"$match": evidence_match},
        {"$match": {"text": {"$type": "string", "$ne": ""}}},
    ]
    if rerank:
        evidence_pipeline += [
            {"$rerank": {"model": NATIVE_RERANK_MODEL, "query": {"text": query}, "path": "text",
                         "numDocsToRerank": EVIDENCE_CANDIDATES}},
            {"$set": {"rerank_score": {"$meta": "score"}}},
        ]
    evidence_pipeline += [{"$limit": final_n}, {"$project": {"text": 1, "metadata": 1, "rerank_score": 1}}]

    pipeline = [
        entry,
        {"$limit": seeds},
        {"$set": {"seed_score": {"$meta": "score"}, "score_details": {"$meta": "scoreDetails"}}},
        # 1º salto: todos os vizinhos diretos, inclusive hubs (um objetivo citado por cem ações).
        {"$lookup": {"from": "kg_entities", "localField": "links", "foreignField": "_id", "as": "hop1",
                     "pipeline": [{"$match": {"client_id": CLIENT_ID, "scope": scope}}, {"$project": compact}]}},
        *([
            # Saltos seguintes: recursivo, mas sem atravessar hubs, ou dois saltos a partir de
            # qualquer ação chegariam a quase o grafo inteiro.
            {"$graphLookup": {
                "from": "kg_entities",
                "startWith": {"$reduce": {"input": {"$filter": {
                    "input": "$hop1", "as": "h", "cond": {"$lte": ["$$h.degree", HUB_DEGREE]}}},
                    "initialValue": [], "in": {"$concatArrays": ["$$value", "$$this.links"]}}},
                "connectFromField": "links", "connectToField": "_id", "as": "hop2",
                "maxDepth": hops - 2, "depthField": "hop",
                "restrictSearchWithMatch": {"client_id": CLIENT_ID, "scope": scope, "degree": {"$lte": HUB_DEGREE}},
            }},
        ] if hops > 1 else []),
        {"$project": {**ENTITY_FIELDS, "seed_score": 1, "score_details": 1,
                      "chunk_ids": {"$slice": ["$chunk_ids", 6]}, "neighbors": neighbor_view}},
        {"$group": {"_id": None, "seeds": {"$push": "$$ROOT"}}},
        {"$set": {"evidence_ids": {"$slice": [{"$concatArrays": [
            {"$setUnion": [seed_chunks]},
            {"$setDifference": [edge_chunks, seed_chunks]},
            {"$setDifference": [neighbor_chunks, {"$concatArrays": [seed_chunks, edge_chunks]}]},
        ]}, EVIDENCE_CANDIDATES]}}},
        {"$set": {"evidence_candidates": {"$size": "$evidence_ids"}}},
        {"$lookup": {"from": "documents", "let": {"ids": "$evidence_ids"},
                     "pipeline": evidence_pipeline, "as": "evidence"}},
        {"$project": {"evidence_ids": 0}},
        {"$set": {"kind": "graph"}},
    ]
    if direct_k:
        from native_retrieval import build_native_pipeline

        direct = build_native_pipeline(query, 15, access_levels, sources, direct_k, use_rerank=rerank)
        pipeline.append({"$unionWith": {"coll": "documents", "pipeline": [*direct, {"$set": {"kind": "direct"}}]}})
    if communities_k:
        pipeline.append({"$unionWith": {"coll": "kg_communities", "pipeline": [
            {"$vectorSearch": {"index": "kg_community_vector_index", "path": "text", "query": query,
                               "model": NATIVE_EMBED_MODEL, "numCandidates": 50, "limit": communities_k,
                               "filter": {"client_id": CLIENT_ID, "scope": scope}}},
            {"$project": {"title": 1, "summary": 1, "size": 1, "sources": 1, "top_entities": 1,
                          "score": {"$meta": "vectorSearchScore"}, "kind": "community"}},
        ]}})
    return pipeline


def _matched_by(score_details):
    details = score_details.get("details") if isinstance(score_details, dict) else None
    if not isinstance(details, list):
        return []
    return sorted(_BRANCH_LABEL[d["inputPipelineName"]] for d in details
                  if isinstance(d, dict) and d.get("inputPipelineName") in _BRANCH_LABEL
                  and isinstance(d.get("rank"), int))


def _label(node):
    code = node.get("code") or ""
    name = node.get("name") or node["_id"]
    return f"{code} {name}".strip() if code and code not in name else name


def parse_graph_rows(rows):
    """Resultado do aggregation -> {seeds, nodes, edges, evidence, communities, evidence_candidates}."""
    graph = next((r for r in rows if r.get("kind") == "graph"), None)
    direct = [r for r in rows if r.get("kind") == "direct"]
    communities = [r for r in rows if r.get("kind") == "community"]
    out = {"seeds": [], "nodes": [], "edges": [], "evidence": [], "evidence_candidates": 0,
           "direct": direct,
           "communities": [{"id": str(c["_id"]), "title": c.get("title", ""), "summary": c.get("summary", ""),
                            "size": c.get("size", 0), "sources": c.get("sources", []),
                            "score": round(float(c.get("score") or 0), 4)} for c in communities]}
    if not graph:
        return out
    nodes: dict[str, dict] = {}
    seed_ids = []
    for s in graph.get("seeds", []):
        seed_ids.append(s["_id"])
        nodes[s["_id"]] = {**{k: s.get(k) for k in ("_id", "name", "type", "code", "description", "sources",
                                                    "degree", "chunk_ids", "relations")},
                           "hop": 0, "seed": True, "score": round(float(s.get("seed_score") or 0), 4),
                           "matched_by": _matched_by(s.get("score_details"))}
    for s in graph.get("seeds", []):
        for n in s.get("neighbors") or []:
            prev = nodes.get(n["_id"])
            if prev is None or n["hop"] < prev["hop"]:
                nodes[n["_id"]] = {**n, "seed": False, "matched_by": [], "score": 0}
    edges = {}
    for node in nodes.values():
        for r in node.get("relations") or []:
            if r["target"] in nodes:
                edges[(node["_id"], r["type"], r["target"])] = {
                    "source": node["_id"], "target": r["target"], "type": r["type"],
                    "sources": r.get("sources", []), "chunk_ids": r.get("chunk_ids", [])}
    out["seeds"] = seed_ids
    out["nodes"] = sorted(nodes.values(), key=lambda n: (n["hop"], -(n.get("degree") or 0), n["_id"]))
    out["edges"] = sorted(edges.values(), key=lambda e: (e["source"] not in seed_ids, e["source"], e["target"]))
    out["evidence"] = graph.get("evidence", [])
    out["evidence_candidates"] = graph.get("evidence_candidates", 0)
    return out


def _edge_order(parsed):
    """Arestas que tocam um ponto de entrada primeiro (na ordem dos pontos), depois o resto."""
    rank = {sid: i for i, sid in enumerate(parsed["seeds"])}
    hop = {n["_id"]: n["hop"] for n in parsed["nodes"]}
    big = len(rank) + 1

    def key(e):
        seed_rank = min(rank.get(e["source"], big), rank.get(e["target"], big))
        return (seed_rank, max(hop[e["source"]], hop[e["target"]]), e["type"], e["source"], e["target"])
    return sorted(parsed["edges"], key=key)


def merge_evidence(parsed, final_n):
    """Trechos do subgrafo + trechos diretos, pelo score do mesmo $rerank, sem repetir chunk.

    Cada trecho volta com `origin`: {"grafo"} e/ou os ramos da busca direta ({"vetorial", "léxico"}).
    """
    from native_retrieval import matched_by

    merged: dict[str, dict] = {}
    for r in parsed["evidence"]:
        merged[str(r["_id"])] = {**r, "origin": {"grafo"}}
    for r in parsed.get("direct", []):
        key = str(r["_id"])
        branches = set(matched_by(r.get("score_details"))) or {"vetorial"}
        if key in merged:
            merged[key]["origin"] |= branches
        else:
            merged[key] = {**r, "origin": branches}
    ranked = sorted(merged.values(), key=lambda r: -(r.get("rerank_score") or 0))
    return ranked[:final_n]


def graph_facts(parsed, max_facts=MAX_FACTS, max_entities=MAX_ENTITY_LINES):
    """Blocos do prompt: entidades (com documentos de origem) e triplas, pontos de entrada primeiro.

    A lista de entidades com `fontes` é o que deixa o modelo responder "em quais documentos X
    aparece"; as triplas ligadas a um ponto de entrada vêm completas (até `max_facts`), o que
    responde enumerações ("quais ações a unidade X conduz") que 8 trechos não cobrem.
    """
    from collections import Counter

    nodes = {n["_id"]: n for n in parsed["nodes"]}
    lines = []
    for e in _edge_order(parsed)[:max_facts]:
        a, b = nodes[e["source"]], nodes[e["target"]]
        src = ", ".join(e.get("sources") or [])
        lines.append(f"- {_label(a)} [{a['type']}] --{e['type']}--> {_label(b)} [{b['type']}]"
                     + (f" (fonte: {src})" if src else ""))
    entity_lines = []
    for n in parsed["nodes"][:max_entities]:
        docs = ", ".join(n.get("sources") or [])
        role = "ENTRADA" if n["seed"] else f"{n['hop']}º salto"
        extra = ""
        if n["seed"]:
            by_type = Counter(r["type"] for r in n.get("relations") or [])
            if by_type:
                extra = f"; {n.get('degree') or 0} conexões, saída: " + ", ".join(
                    f"{t} ×{c}" for t, c in by_type.most_common())
        desc = f": {n['description'][:200]}" if n["seed"] and n.get("description") else ""
        entity_lines.append(f"- [{role}] {_label(n)} [{n['type']}] (documentos: {docs}{extra}){desc}")
    return entity_lines, lines


def chunk_provenance(parsed):
    """chunk_id -> nomes das entidades/arestas do subgrafo que citam aquele trecho."""
    nodes = {n["_id"]: n for n in parsed["nodes"]}
    via: dict[str, list] = {}

    def add(cid, label):
        lst = via.setdefault(str(cid), [])
        if label not in lst:
            lst.append(label)

    for n in parsed["nodes"]:
        for cid in n.get("chunk_ids") or []:
            add(cid, _label(n))
    for e in parsed["edges"]:
        label = f"{_label(nodes[e['source']])} → {_label(nodes[e['target']])}"
        for cid in e.get("chunk_ids") or []:
            add(cid, label)
    return via


def draw_view(parsed, max_nodes=MAX_DRAW_NODES):
    """Subgrafo enxuto para a animação da UI (sem textos longos).

    O recorte é estratificado por salto: cortar os primeiros `max_nodes` da lista (ordenada por
    salto) deixava o 2º salto sempre de fora quando o 1º era grande. `hop_counts` leva os totais
    reais, que a legenda mostra mesmo quando o desenho é um recorte.
    """
    by_hop = {}
    for n in parsed["nodes"]:
        by_hop.setdefault(min(n["hop"], 2), []).append(n)
    seeds = by_hop.get(0, [])
    room = max(0, max_nodes - len(seeds))
    hop1, hop2 = by_hop.get(1, []), by_hop.get(2, [])
    take2 = min(len(hop2), room // 3 if hop1 else room)
    take1 = min(len(hop1), room - take2)
    take2 = min(len(hop2), room - take1)
    keep = seeds + hop1[:take1] + hop2[:take2]
    ids = {n["_id"] for n in keep}
    return {
        "nodes": [{"id": n["_id"], "label": _label(n)[:60], "type": n["type"], "hop": n["hop"],
                   "seed": n["seed"], "matched_by": n.get("matched_by", []),
                   "sources": n.get("sources") or []} for n in keep],
        "edges": [{"source": e["source"], "target": e["target"], "type": e["type"]}
                  for e in parsed["edges"] if e["source"] in ids and e["target"] in ids][:90],
        "total_nodes": len(parsed["nodes"]),
        "hop_counts": [len(by_hop.get(h, [])) for h in (0, 1, 2)],
        "total_edges": len(parsed["edges"]),
    }


def run_graph(db, query, *, final_n=8, rerank=True, access_levels=None, sources=None, scope="base"):
    """Executa o aggregation. Levanta em falha: o chamador decide a degradação."""
    pipeline = build_graph_pipeline(query, final_n=final_n, rerank=rerank,
                                    access_levels=access_levels, sources=sources, scope=scope)
    rows = list(db["kg_entities"].aggregate(pipeline))
    return parse_graph_rows(rows), pipeline
