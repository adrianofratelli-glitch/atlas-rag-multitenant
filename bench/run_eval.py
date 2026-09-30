"""Run the gold set against two Atlas stores (PDF-ingested vs Markdown-ingested).

Both stores are queried from a single process so the query-embedding cache in
agent.py is shared: each question is embedded once, not once per store.
"""
import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agent  # noqa: E402
from bench.llm import complete  # noqa: E402

HERE = Path(__file__).parent
GOLD = HERE / "goldset.json"
OUT = HERE / "results.json"

# Bancos do comparativo via ambiente: o repositório não carrega nome de tenant.
STORES = [
    {"key": "pdf", "label": "PDF (PyPDFLoader)", "db": os.environ["BENCH_DB_PDF"]},
    {"key": "md", "label": "Markdown (pymupdf4llm)", "db": os.environ["BENCH_DB_MD"]},
]

ANSWER_SYSTEM = """Você é um assistente que responde exclusivamente com base no
CONTEXTO fornecido. Se o contexto não contiver a informação, responda
"Não encontrei essa informação no documento." Não use conhecimento externo.
Seja direto e cite números e datas exatamente como aparecem no contexto."""

JUDGE_SYSTEM = """Você avalia respostas de um sistema RAG contra um gabarito.
Seja rigoroso e determinístico. Responda SOMENTE com JSON válido, sem cercas."""

JUDGE_TEMPLATE = """Pergunta: {pergunta}

Gabarito (verdade extraída do documento original):
{gabarito}

Resposta do sistema:
{resposta}

Avalie de 0 a 5:
- "acuracia": os fatos/números da resposta batem com o gabarito (5 = idênticos,
  0 = errados ou "não encontrei").
- "completude": cobre tudo que o gabarito cobre.
- "fidelidade": ausência de alucinação — nada afirmado além do gabarito/documento.
  Uma recusa honesta ("não encontrei") vale 5 aqui, mas 0 em acurácia.

Formato:
{{"acuracia": n, "completude": n, "fidelidade": n, "justificativa": "1 frase"}}"""


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9áàâãéêíóôõúç ]", " ", s.lower())


def evidence_recall(evidencia: str, context: str) -> float:
    """Fraction of the gold evidence's content words present in the retrieved context."""
    words = [w for w in _norm(evidencia).split() if len(w) > 3]
    if not words:
        return 0.0
    ctx = set(_norm(context).split())
    return sum(1 for w in words if w in ctx) / len(words)


def answer(question: str, context: str) -> str:
    return complete(ANSWER_SYSTEM, f"CONTEXTO:\n{context}\n\nPERGUNTA: {question}", max_tokens=1200)


def judge(q: dict, resposta: str) -> dict:
    raw = complete(
        JUDGE_SYSTEM,
        JUDGE_TEMPLATE.format(pergunta=q["pergunta"], gabarito=q["gabarito"], resposta=resposta),
        max_tokens=600,
    )
    raw = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    return json.loads(raw)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sleep", type=float, default=21.0,
                    help="pause between Voyage rerank calls (free tier: 3 RPM)")
    args = ap.parse_args()

    gold = json.loads(GOLD.read_text(encoding="utf-8"))["perguntas"]
    results = []

    for q in gold:
        row = {"id": q["id"], "pergunta": q["pergunta"], "tipo": q["tipo"],
               "gabarito": q["gabarito"], "evidencia": q["evidencia"], "stores": {}}
        for store in STORES:
            agent.DB_NAME = store["db"]
            t0 = time.perf_counter()
            context, sources, stats = agent.retrieve_context(q["pergunta"])
            retrieval_ms = int((time.perf_counter() - t0) * 1000)

            resposta = answer(q["pergunta"], context)
            scores = judge(q, resposta)

            row["stores"][store["key"]] = {
                "resposta": resposta,
                "retrieval_ms": retrieval_ms,
                "evidence_recall": round(evidence_recall(q["evidencia"], context), 3),
                "top_rerank": max((s.get("rerank_score", 0) for s in sources), default=0),
                "mean_rerank": round(
                    sum(s.get("rerank_score", 0) for s in sources) / max(len(sources), 1), 4),
                "n_sources": len(sources),
                "vector_hits": stats.get("vector_hits", 0),
                "lexical_hits": stats.get("lexical_hits", 0),
                "fused": stats.get("fused", 0),
                "pages": sorted({str(s.get("page")) for s in sources}),
                **scores,
            }
            print(f"q{q['id']:>2} {store['key']:>3} "
                  f"acc={scores['acuracia']} comp={scores['completude']} "
                  f"fid={scores['fidelidade']} rec={row['stores'][store['key']]['evidence_recall']}")
            time.sleep(args.sleep)

        results.append(row)
        OUT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n{len(results)} questões avaliadas -> {OUT}")


if __name__ == "__main__":
    main()
