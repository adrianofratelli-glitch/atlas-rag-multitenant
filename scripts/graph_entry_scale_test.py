"""Teste de escala da busca de entrada do Graph-RAG: autoEmbed + $rankFusion + travessia, ponta a ponta.

    python scripts/graph_entry_scale_test.py               # 100 mil entidades; apaga o banco no fim
    python scripts/graph_entry_scale_test.py --n 20000 --keep

Complementa `graph_scale_test.py` (só travessia, entradas sorteadas): aqui as entidades têm texto
sintético variado, o Atlas embeda tudo pelo índice `autoEmbed` (voyage-4) e cada consulta roda o
pipeline real de `graph_retrieval.build_graph_pipeline` — `$rankFusion` ($vectorSearch + $search)
nas entidades, `$lookup` + `$graphLookup` — sem os estágios que dependem dos chunks do corpus.

Mede: tempo até os índices ficarem prontos (custo de embedar N entidades), latência da busca de
entrada sozinha e do pipeline de entrada + travessia. Banco `<DB_NAME>_graph_entry_test`, apagado no
fim salvo `--keep`. Custo: N textos curtos (~25 tokens) embedados pela plataforma.
"""
import argparse
import random
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import CLIENT_ID, DB_NAME, NATIVE_EMBED_MODEL, assert_writable_db  # noqa: E402
from db import get_client  # noqa: E402
import graph_retrieval as gr  # noqa: E402
from scripts.graph_scale_test import generate  # noqa: E402

VERBS = ["Implantar", "Modernizar", "Integrar", "Migrar", "Revisar", "Automatizar", "Contratar", "Ampliar",
         "Desenvolver", "Monitorar", "Padronizar", "Capacitar", "Atualizar", "Consolidar", "Digitalizar"]
OBJECTS = ["sistema de protocolo", "portal de serviços", "rede de dados", "central de atendimento", "painel de gestão",
           "plataforma de nuvem", "política de segurança", "catálogo de serviços", "processo de compras",
           "inventário de ativos", "repositório de documentos", "serviço de identidade", "data center",
           "aplicativo móvel", "barramento de integração", "módulo financeiro", "gestão de contratos"]
UNITS = ["Coordenadoria", "Diretoria", "Divisão", "Núcleo", "Secretaria", "Gerência", "Comitê"]
AREAS = ["de Infraestrutura", "de Sistemas", "de Dados", "de Segurança", "de Atendimento", "de Governança",
         "de Contratos", "de Inovação", "de Suporte", "de Planejamento", "de Arquitetura", "de Pessoas"]
QUERIES = ["Quem conduz a modernização do portal de serviços?", "Quais ações migram sistemas para a nuvem?",
           "Que unidade cuida da política de segurança?", "Ações de integração do barramento",
           "Qual diretoria responde pelo data center?", "Projetos de automação do processo de compras",
           "Capacitação em gestão de contratos", "Quem monitora o inventário de ativos?"]


def textify(doc, rng):
    if doc["type"] == "unidade":
        name = f"{rng.choice(UNITS)} {rng.choice(AREAS)} {doc['_id'][1:]}"
    else:
        name = f"{rng.choice(VERBS)} o {rng.choice(OBJECTS)} {doc['_id'][1:]}"
    doc["name"] = name
    doc["code"] = f"AX{doc['_id'][1:]}" if doc["type"] == "acao" else ""
    doc["aliases"] = []
    doc["description"] = f"{name}, ligado a {len(doc['links'])} outras entidades do plano."
    doc["text"] = f"{name} [{doc['type']}]: {doc['description']}"
    return doc


def wait_ready(coll, timeout_s):
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        states = {ix["name"]: (ix.get("status"), ix.get("queryable")) for ix in coll.list_search_indexes()}
        if states and all(s == "READY" and q for s, q in states.values()):
            return time.time() - t0
        time.sleep(15)
    return None


def timed(coll, pipeline_fn, runs):
    times = []
    for i in range(runs):
        q = QUERIES[i % len(QUERIES)] + f" {i}"
        t0 = time.perf_counter()
        list(coll.aggregate(pipeline_fn(q)))
        times.append((time.perf_counter() - t0) * 1000)
    times.sort()
    return {"p50": round(statistics.median(times)), "p95": round(times[int(len(times) * 0.95) - 1])}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--n", type=int, default=100000)
    ap.add_argument("--runs", type=int, default=24)
    ap.add_argument("--index-timeout", type=int, default=5400)
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args(argv)
    db_name = f"{DB_NAME}_graph_entry_test"
    assert_writable_db(db_name)
    client = get_client()
    db = client[db_name]
    coll = db["kg_entities"]
    rng = random.Random(3)
    try:
        coll.drop()
        t0 = time.perf_counter()
        batch = []
        for doc in generate(args.n):
            batch.append(textify(doc, rng))
            if len(batch) == 5000:
                coll.insert_many(batch, ordered=False)
                batch = []
        if batch:
            coll.insert_many(batch, ordered=False)
        coll.create_index([("links", 1)])
        print(f"n={args.n}: inserido em {time.perf_counter() - t0:.0f}s", flush=True)
        coll.create_search_index({"name": "kg_vector_index", "type": "vectorSearch", "definition": {"fields": [
            {"type": "autoEmbed", "modality": "text", "path": "text", "model": NATIVE_EMBED_MODEL},
            {"type": "filter", "path": "client_id"}, {"type": "filter", "path": "scope"},
            {"type": "filter", "path": "type"}, {"type": "filter", "path": "sources"}]}})
        coll.create_search_index({"name": "kg_text_index", "type": "search", "definition": {"mappings": {
            "dynamic": False, "fields": {"name": {"type": "string", "analyzer": "lucene.portuguese"},
                                         "aliases": {"type": "string"}, "code": {"type": "token"},
                                         "description": {"type": "string", "analyzer": "lucene.portuguese"},
                                         "client_id": {"type": "token"}, "scope": {"type": "token"}}}}})
        ready = wait_ready(coll, args.index_timeout)
        if ready is None:
            print(f"índices não ficaram prontos em {args.index_timeout}s", flush=True)
            return 2
        print(f"índices prontos em {ready:.0f}s (autoEmbed de {args.n} entidades)", flush=True)
        entry = timed(coll, lambda q: gr.build_graph_pipeline(q, direct_k=0, communities_k=0)[:3], args.runs)
        vector = timed(coll, lambda q: [gr.build_graph_pipeline(q)[0]["$rankFusion"]["input"]["pipelines"]["vector"][0],
                                        {"$project": {"_id": 1}}], args.runs)
        full = timed(coll, lambda q: gr.build_graph_pipeline(q, direct_k=0, communities_k=0)[:9], args.runs)
        print(f"$vectorSearch (autoEmbed) sozinho: {vector}", flush=True)
        print(f"$rankFusion de entrada (vetorial + léxico): {entry}", flush=True)
        print(f"entrada + $lookup + $graphLookup + $group: {full}", flush=True)
    finally:
        if not args.keep:
            client.drop_database(db_name)
            print(f"banco {db_name} apagado", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
