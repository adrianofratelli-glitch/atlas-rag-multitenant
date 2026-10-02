"""Eval de retrieval SEM LLM (roda no venv principal): recall@k, MRR, nDCG + compare-mode.

Modos: (a) vector only, (b) vector + rerank-2, (c) hybrid RRF + rerank-2, (d) hybrid RRF sem rerank.
Variação de k = tamanho do pool de candidatos (top_k do $vectorSearch/$search).
Chamadas ao Atlas/Voyage são cacheadas em eval/reports/retrieval_cache.json (--refresh refaz).

  python -m eval.run_retrieval [--ks 15] [--limit N] [--refresh]
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, ".")
from dotenv import load_dotenv

load_dotenv()

from eval.common import REPORTS, load_golden, chunk_has_anchor  # noqa: E402

MODES = {
    "a_vector": dict(use_lexical=False, use_rerank=False),
    "b_vector+rerank": dict(use_lexical=False, use_rerank=True),
    "c_hybrid+rerank": dict(use_lexical=True, use_rerank=True),
    "d_hybrid_no_rerank": dict(use_lexical=True, use_rerank=False),
}
FINAL_N = 8
# EVAL_TAG separa cache e relatórios de execuções com configurações diferentes
# (ex.: EVAL_TAG=_native com RAG_NATIVE=1 e DB_NAME do banco v2), sem sobrescrever o baseline.
TAG = os.getenv("EVAL_TAG", "")
# EVAL_SOURCES=a,b restringe a recuperação a esses documentos (compara corpora de tamanhos diferentes no mesmo conteúdo).
SOURCES = [x for x in os.getenv("EVAL_SOURCES", "").split(",") if x] or None
CACHE = REPORTS / f"retrieval_cache{TAG}.json"


def collect(rows, ks, refresh):
    import agent
    cache = {} if refresh or not CACHE.exists() else json.loads(CACHE.read_text())
    for n, r in enumerate(rows, 1):
        for k in ks:
            for mode, kw in MODES.items():
                if r["kind"] != "answerable" and mode != "c_hybrid+rerank":
                    continue  # sem-resposta/cross-tenant só servem para calibrar a recusa
                key = f"{r['id']}|{mode}|{k}"
                hit = cache.get(key)
                if hit is not None:
                    if hit.get("question") not in (None, r["question"]):
                        raise SystemExit(
                            f"cache desalinhado em {key}: foi gravado para outra pergunta "
                            f"({hit['question'][:60]!r}). Rode com --refresh.")
                    continue
                cap = []
                t0 = time.perf_counter()
                _, _, stats = agent.retrieve_context(r["question"], top_k=k, final_n=FINAL_N, _capture=cap,
                                                     sources=SOURCES, **kw)
                cache[key] = {
                    "question": r["question"],  # trava: o cache é por id, e id só vale com a mesma pergunta
                    "ms": int((time.perf_counter() - t0) * 1000),
                    "rerank_degraded": stats.get("rerank_degraded", False),
                    "chunks": [{"id": c["chunk_id"], "text": c["text"], "score": c.get("rerank_score"),
                                "page": c["metadata"].get("page")} for c in cap],
                }
        CACHE.parent.mkdir(exist_ok=True)
        CACHE.write_text(json.dumps(cache, ensure_ascii=False))
        print(f"[{n}/{len(rows)}] {r['id']}", flush=True)
    return cache


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ks", default="15")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--out", default=str(REPORTS / f"retrieval{TAG}.md"))
    a = ap.parse_args()
    ks = [int(x) for x in a.ks.split(",")]
    every = load_golden()
    if a.limit:
        every = every[: a.limit]
    cache = collect(every, ks, a.refresh)

    from evalkit import compare
    answerable = [r for r in every if r["kind"] == "answerable"]
    ds = REPORTS / "retrieval_dataset.jsonl"
    ds.write_text("".join(json.dumps({"id": r["id"], "query": r["question"], "relevant": [r["id"]],
                                      "reference": r["reference"]}, ensure_ascii=False) + "\n" for r in answerable))
    by_id = {r["id"]: r for r in answerable}

    def target(mode, k):
        def fn(rec):
            gold = by_id[rec["id"]]
            hit = cache[f"{rec['id']}|{mode}|{k}"]
            ids = [rec["id"] if chunk_has_anchor(c["text"], gold["anchor"]) else c["id"] for c in hit["chunks"]]
            # Vários chunks (sobreposição 150) podem conter a mesma âncora: conta o id relevante uma vez,
            # senão o DCG soma o mesmo ganho repetido e o nDCG passa de 1.
            return {"retrieved": list(dict.fromkeys(ids))}
        return fn

    metrics = ["recall@1", "recall@3", "recall@5", "recall@8", "mrr", "ndcg@8"]
    md = ["# Retrieval eval (sem LLM)\n",
          f"Golden: `eval/golden.jsonl` — {len(answerable)} perguntas respondíveis (sintéticas). "
          f"Relevância = chunk recuperado contém o trecho-âncora do documento. final_n={FINAL_N}.\n"]
    out = {}
    for k in ks:
        targets = {m: target(m, k) for m in MODES}
        res = compare(str(ds), targets, metrics)
        md.append(f"\n## Pool de candidatos k={k}\n")
        md.append(res["markdown"] if isinstance(res, dict) and "markdown" in res else str(res))
        out[k] = res if isinstance(res, dict) else {}
    lat = {m: sum(cache[f"{r['id']}|{m}|{ks[0]}"]["ms"] for r in answerable) / max(1, len(answerable)) for m in MODES}
    md.append("\n## Latência média de retrieval (ms, k=%d)\n" % ks[0])
    md.append("O embedding da pergunta é cacheado entre modos: o primeiro modo executado (a_vector) paga a chamada à Voyage, "
              "os demais não. Compare só a diferença entre com e sem rerank/léxico, não o valor absoluto de `a`.\n")
    md.append("| modo | ms |\n|---|---|\n" + "\n".join(f"| {m} | {v:.0f} |" for m, v in lat.items()))
    degraded = sum(1 for v in cache.values() if v["rerank_degraded"])
    md.append(f"\nRerank degradado (fallback RRF) em {degraded} chamadas.\n")

    # Calibração do piso da recusa: escolhida SÓ no split `calib` e verificada no split `test`,
    # que é o que o relatório de recusa usa. Sem essa separação o piso seria escolhido no mesmo
    # conjunto que o avalia, e a taxa de recusa sairia otimista por construção.
    k0 = ks[0]

    def tops_for(split):
        out = {"answerable": [], "no_answer": [], "cross_tenant": []}
        for r in every:
            if r["split"] != split:
                continue
            hit = cache.get(f"{r['id']}|c_hybrid+rerank|{k0}")
            if hit and hit["chunks"]:
                out[r["kind"]].append(max(c["score"] or 0 for c in hit["chunks"]))
        return out

    def table(tops, title):
        lines = [f"\n### {title}\n", "| tipo | n | min | mediana | max |", "|---|---|---|---|---|"]
        for kind, v in tops.items():
            if v:
                v = sorted(v)
                lines.append(f"| {kind} | {len(v)} | {v[0]:.3f} | {v[len(v) // 2]:.3f} | {v[-1]:.3f} |")
        bad = tops["no_answer"] + tops["cross_tenant"]
        lines += ["", "| piso | respondíveis recusadas (falso-recusa) | sem-resposta+cross-tenant recusadas |",
                  "|---|---|---|"]
        for th in (0.3, 0.5, 0.6, 0.65, 0.7):
            fa = sum(1 for x in tops["answerable"] if x < th)
            rb = sum(1 for x in bad if x < th)
            lines.append(f"| {th} | {fa}/{len(tops['answerable'])} | {rb}/{len(bad)} |")
        return lines, bad

    calib, test = tops_for("calib"), tops_for("test")
    md.append("\n## Piso da recusa (`RAG_MIN_RERANK_SCORE`): score máximo do rerank-2 por tipo\n")
    md.append("O piso é **escolhido no split `calib`** e conferido no split `test`, que é o usado pelo "
              "relatório de recusa. As duas tabelas abaixo devem contar a mesma história; se divergirem "
              "muito, o piso está sobreajustado ao `calib`.")
    lines, bad_c = table(calib, "Split `calib` (escolha do piso)")
    md += lines
    lines, _ = table(test, "Split `test` (verificação; é o que o relatório de recusa mede)")
    md += lines
    best = None
    for th in (0.3, 0.5, 0.6, 0.65, 0.7):
        fa = sum(1 for x in calib["answerable"] if x < th)
        rb = sum(1 for x in bad_c if x < th)
        if fa == 0 and (best is None or rb > best[1]):
            best = (th, rb)
    if best:
        md.append(f"\nMaior piso sem falsa recusa no `calib`: **{best[0]}** "
                  f"({best[1]}/{len(bad_c)} recusas devidas). É o valor sugerido para `RAG_MIN_RERANK_SCORE`.")
    pathlib = REPORTS / f"retrieval_results{TAG}.json"
    pathlib.write_text(json.dumps({str(k): v for k, v in out.items()}, ensure_ascii=False, default=str))
    open(a.out, "w", encoding="utf-8").write("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    main()
