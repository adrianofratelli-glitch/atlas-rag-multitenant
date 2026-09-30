"""Gera as respostas do sistema para o golden (venv principal; usa Claude) e mede a taxa de recusa.

Reusa o retrieval já cacheado por run_retrieval (hybrid + rerank-2, k=--k) e o mesmo prompt/gate do
backend (SYSTEM_PROMPT_STATIC, _is_obviously_out_of_scope, insufficient_evidence). A recusa é
classificada por um juiz LLM, com a regex só como pré-filtro (eval/refusal_judge.py).

Saída: eval/reports/answers_<gate>_<split>.jsonl (entrada do Ragas) e refusal_<gate>_<split>.md.

  python -m eval.run_generation [--k 15] [--gate on|off] [--split test|calib|all]
"""
import argparse
import json
import sys

sys.path.insert(0, ".")
from dotenv import load_dotenv

load_dotenv()

from langchain_core.messages import HumanMessage, SystemMessage  # noqa: E402

from eval.common import REPORTS, load_golden  # noqa: E402
from eval.refusal_judge import classify, judge_llm  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=15)
    ap.add_argument("--gate", choices=["on", "off"], default="off",
                    help="RAG_REFUSE_WEAK_EVIDENCE (recusa por score do rerank)")
    ap.add_argument("--split", choices=["test", "calib", "all"], default="test",
                    help="test = os números do relatório; calib = o conjunto que calibra o piso")
    a = ap.parse_args()
    cache = json.loads((REPORTS / "retrieval_cache.json").read_text())

    import agent
    agent.REFUSE_WEAK_EVIDENCE = a.gate == "on"
    from backend import api
    from config import CLIENT_NAME, DOCUMENT_TITLE, SYSTEM_PROMPT_EXTRA

    llm = api._get_llm()
    judge = judge_llm()
    system = api.SYSTEM_PROMPT_STATIC.format(document_title=DOCUMENT_TITLE, client_name=CLIENT_NAME,
                                             extra=SYSTEM_PROMPT_EXTRA)
    rows = []
    golden = [r for r in load_golden() if a.split == "all" or r["split"] == a.split]
    for r in golden:
        hit = cache.get(f"{r['id']}|c_hybrid+rerank|{a.k}")
        chunks = hit["chunks"] if hit else []
        sources = [{"rerank_score": c["score"]} for c in chunks]
        stats = {"reranked": len(chunks)} if chunks else {"mode": "no_context"}
        gate = None
        if api._is_obviously_out_of_scope(r["question"]):
            gate, answer = "scope_redirect", api._scope_reply()
        elif agent.insufficient_evidence(sources, stats):
            gate, answer = "no_evidence", api.REFUSAL_MESSAGE
        else:
            ctx = "\n\n---\n\n".join(f"[Página {c['page']} | documento]\n{c['text']}" for c in chunks)
            msg = llm.invoke([SystemMessage(content=f"{system}\n\nCONTEXTO:\n{ctx}"),
                              HumanMessage(content=r["question"])])
            answer = msg.content if isinstance(msg.content, str) else "".join(
                b.get("text", "") for b in msg.content if isinstance(b, dict))
        verdict = classify(r["question"], answer, judge, gate=gate)
        rows.append({"id": r["id"], "kind": r["kind"], "split": r["split"], "question": r["question"],
                     "reference": r["reference"], "answer": answer, "contexts": [c["text"] for c in chunks],
                     "gate": gate, "refused": verdict["refused"], "verdict": verdict,
                     "should_refuse": r["should_refuse"]})
        print(r["id"], r["kind"], "refused" if verdict["refused"] else "answered",
              verdict["source"], flush=True)
    (REPORTS / f"answers_{a.gate}_{a.split}.jsonl").write_text(
        "".join(json.dumps(x, ensure_ascii=False) + "\n" for x in rows))

    from eval.report_refusal import render
    md = render(rows, gate=a.gate, split=a.split, threshold=agent.MIN_RERANK_SCORE)
    (REPORTS / f"refusal_{a.gate}_{a.split}.md").write_text(md)
    print(md)


if __name__ == "__main__":
    main()
