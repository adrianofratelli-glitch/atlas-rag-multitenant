"""Teste de escala da travessia do Graph-RAG: grafo sintético de 10 mil a 1 milhão de entidades.

    python scripts/graph_scale_test.py                    # 10k, 100k, 1M; apaga o banco no fim
    python scripts/graph_scale_test.py --sizes 10000 --keep

Grava num banco próprio `<DB_NAME>_graph_scale_test` (termina em _test: passa pela guarda sem
flag) e o apaga no fim, salvo `--keep`. Não toca no banco da demo.

O que mede: só a travessia (`$lookup` 1º salto + `$graphLookup` 2º salto + `$group`), com o
mesmo formato de documento de `kg_entities` (`links`, `degree`, `relations`) e o mesmo corte de
hubs do `graph_retrieval.py`. A busca de entrada ($vectorSearch/autoEmbed) fica de fora de
propósito: embedar 1 milhão de entidades sintéticas custaria embedding sem medir nada do grafo,
e a latência do $vectorSearch em escala já é documentada à parte. As entradas são sorteadas.

Topologia: anexação preferencial (Barabási–Albert, m=3), que gera hubs como os do grafo real
(um objetivo ligado a cem ações). Cada consulta parte de 6 entradas, como a aba.
Também roda a variante sem o corte de hubs, para mostrar por que o corte existe.
"""
import argparse
import os
import random
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import CLIENT_ID, DB_NAME, assert_writable_db  # noqa: E402
from db import get_client  # noqa: E402

TYPES = ["acao"] * 5 + ["unidade", "objetivo", "sistema", "norma", "indicador"]
RELS = ["responsavel_por", "contribui_para", "depende_de", "utiliza", "relacionado_a"]
HUB = int(os.getenv("GRAPH_HUB_DEGREE", "40"))


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def generate(n, m=3, seed=1):
    """Barabási–Albert: cada nó novo liga a m nós existentes, com chance proporcional ao grau."""
    rng = random.Random(seed)
    links = [set() for _ in range(n)]
    out = [[] for _ in range(n)]
    targets_pool = list(range(m))
    for i in range(m):
        for j in range(i):
            links[i].add(j)
            links[j].add(i)
            out[i].append(j)
    for v in range(m, n):
        chosen = set()
        while len(chosen) < m:
            chosen.add(rng.choice(targets_pool))
        for t in chosen:
            links[v].add(t)
            links[t].add(v)
            out[v].append(t)
            targets_pool.extend((v, t))
    for v in range(n):
        yield {
            "_id": f"e{v}", "client_id": CLIENT_ID, "scope": "base", "type": rng.choice(TYPES),
            "name": f"Entidade {v}", "description": "entidade sintética do teste de escala",
            "links": [f"e{x}" for x in sorted(links[v])], "degree": len(links[v]),
            "relations": [{"target": f"e{t}", "type": rng.choice(RELS), "chunk_ids": []} for t in out[v]],
            "chunk_ids": [], "sources": ["SINTETICO"],
        }


def traversal_pipeline(seed_ids, hub_cap=True):
    compact = {"_id": 1, "degree": 1, "links": 1, "type": 1}
    hop2_start = {"$reduce": {
        "input": {"$filter": {"input": "$hop1", "as": "h", "cond": {"$lte": ["$$h.degree", HUB]}}}
        if hub_cap else "$hop1",
        "initialValue": [], "in": {"$concatArrays": ["$$value", "$$this.links"]}}}
    restrict = {"client_id": CLIENT_ID, "scope": "base"}
    if hub_cap:
        restrict["degree"] = {"$lte": HUB}
    return [
        {"$match": {"_id": {"$in": seed_ids}}},
        {"$lookup": {"from": "kg_entities", "localField": "links", "foreignField": "_id", "as": "hop1",
                     "pipeline": [{"$match": {"client_id": CLIENT_ID, "scope": "base"}}, {"$project": compact}]}},
        {"$graphLookup": {"from": "kg_entities", "startWith": hop2_start, "connectFromField": "links",
                          "connectToField": "_id", "as": "hop2", "maxDepth": 0, "depthField": "hop",
                          "restrictSearchWithMatch": restrict}},
        {"$project": {"n1": {"$size": "$hop1"}, "n2": {"$size": "$hop2"},
                      "neighbors": {"$slice": [{"$concatArrays": ["$hop1._id", "$hop2._id"]}, 60]}}},
        {"$group": {"_id": None, "n1": {"$sum": "$n1"}, "n2": {"$sum": "$n2"}, "seeds": {"$sum": 1}}},
    ]


def measure(coll, n, runs, hub_cap):
    rng = random.Random(7)
    times, sizes, errors = [], [], 0
    for _ in range(runs):
        seeds = [f"e{rng.randrange(n)}" for _ in range(6)]
        t0 = time.perf_counter()
        try:
            row = next(iter(coll.aggregate(traversal_pipeline(seeds, hub_cap), allowDiskUse=True)), {})
        except Exception as exc:  # noqa: BLE001 — ex.: limite de memória do $graphLookup
            errors += 1
            last = str(exc)[:160]
            continue
        times.append((time.perf_counter() - t0) * 1000)
        sizes.append(row.get("n1", 0) + row.get("n2", 0))
    res = {"runs": runs, "errors": errors}
    if times:
        times.sort()
        res.update(p50=round(statistics.median(times)), p95=round(times[int(len(times) * 0.95) - 1]),
                   max=round(times[-1]), nodes_p50=int(statistics.median(sizes)), nodes_max=max(sizes))
    if errors:
        res["error"] = last
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sizes", default="10000,100000,1000000")
    ap.add_argument("--runs", type=int, default=30)
    ap.add_argument("--keep", action="store_true", help="não apaga o banco de teste no fim")
    args = ap.parse_args(argv)
    db_name = f"{DB_NAME}_graph_scale_test"
    assert_writable_db(db_name)
    client = get_client()
    db = client[db_name]
    results = []
    try:
        for n in [int(x) for x in args.sizes.split(",")]:
            coll = db["kg_entities"]
            coll.drop()
            t0 = time.perf_counter()
            batch = []
            for doc in generate(n):
                batch.append(doc)
                if len(batch) == 5000:
                    coll.insert_many(batch, ordered=False)
                    batch = []
            if batch:
                coll.insert_many(batch, ordered=False)
            coll.create_index([("links", 1)])
            stats = db.command("collStats", "kg_entities")
            top = list(coll.find({}, {"degree": 1}).sort("degree", -1).limit(1))[0]["degree"]
            hubs = coll.count_documents({"degree": {"$gt": HUB}})
            log(f"n={n}: carregado em {time.perf_counter() - t0:.0f}s, {stats['size'] / 1e6:.0f} MB, "
                f"maior grau {top}, {hubs} hubs (> {HUB})")
            capped = measure(coll, n, args.runs, True)
            uncapped = measure(coll, n, max(5, args.runs // 3), False)
            log(f"  com corte de hubs: {capped}")
            log(f"  sem corte de hubs: {uncapped}")
            results.append((n, round(stats["size"] / 1e6), top, hubs, capped, uncapped))
    finally:
        if not args.keep:
            client.drop_database(db_name)
            log(f"banco {db_name} apagado")
    print("\n| entidades | MB | maior grau | hubs | p50 / p95 (ms), com corte | nós alcançados (p50) | sem corte: p50 / p95 (ms), nós (p50), erros |")
    print("|---|---|---|---|---|---|---|")
    for n, mb, top, hubs, c, u in results:
        unc = f"{u.get('p50', '—')} / {u.get('p95', '—')}, {u.get('nodes_p50', '—')}, {u['errors']}/{u['runs']}"
        print(f"| {n:,} | {mb} | {top} | {hubs} | {c.get('p50')} / {c.get('p95')} | {c.get('nodes_p50')} | {unc} |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
