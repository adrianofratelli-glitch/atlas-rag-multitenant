"""Métricas Ragas (faithfulness, context precision, answer relevancy) — roda no VENV SEPARADO.

Entrada: eval/reports/answers_off_all.jsonl (gerado por run_generation no venv principal).
Só avalia as perguntas respondíveis que o sistema respondeu (não recusou).

  <venv-eval>/bin/python -m eval.run_ragas
"""
import json
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "..")  # _shared via `pip install -e` no venv separado; fallback para caminho relativo
from dotenv import load_dotenv

load_dotenv()

from evalkit import run_eval  # noqa: E402
from evalkit.ragas_metrics import build_judge  # noqa: E402
from eval.common import REPORTS  # noqa: E402


def _judge():
    """Juiz com teto de saída maior que o default do _shared (2048).

    As respostas deste PoV trazem tabelas markdown longas; a decomposição em statements do
    faithfulness estourava 2048 e o Ragas levantava LLMDidNotFinishException, descartando
    a maioria dos itens (os números saíam de uma amostra enviesada pelas respostas curtas).
    """
    import os

    os.environ.setdefault("EVAL_JUDGE_MAX_TOKENS", "8192")
    judge = build_judge()
    judge.langchain_llm.max_tokens = int(os.environ["EVAL_JUDGE_MAX_TOKENS"])
    return judge


def main():
    # answers_off = sem gate por score (comportamento padrão). Gerado com --split all para o
    # Ragas ver o golden inteiro; o relatório de recusa usa só o split `test`.
    rows = [json.loads(l) for l in open(REPORTS / "answers_off_all.jsonl", encoding="utf-8")]
    rows = [r for r in rows if r["kind"] == "answerable" and not r["refused"]]
    ds = REPORTS / "ragas_dataset.jsonl"
    ds.write_text("".join(json.dumps({"id": r["id"], "query": r["question"], "relevant": [], "reference": r["reference"]},
                                     ensure_ascii=False) + "\n" for r in rows))
    by_id = {r["id"]: r for r in rows}
    res = run_eval(str(ds), lambda rec: {"retrieved": [], "answer": by_id[rec["id"]]["answer"],
                                         "contexts": by_id[rec["id"]]["contexts"]},
                   ["faithfulness", "context_precision", "answer_relevancy"],
                   judge=_judge(), out_json=str(REPORTS / "ragas.json"))
    md = ["# Ragas\n",
          f"{len(rows)} respostas avaliadas (respondíveis, não recusadas).\n",
          "**Limitação:** o dataset (perguntas/gabaritos) foi escrito por Claude e o juiz do Ragas também é Claude "
          "(além de o gerador das respostas ser Claude Sonnet). Juiz e gerador compartilham viés; trate os números como "
          "indicador relativo entre configurações, não como taxa real de qualidade.\n", res.get("markdown", str(res))]
    if res.get("warnings"):
        md.append("\nAvisos do evalkit: " + "; ".join(map(str, res["warnings"])))
    items = res.get("items", [])
    vazios = {m: sum(1 for i in items if i.get("scores", {}).get(m) is None)
              for m in ("faithfulness", "context_precision", "answer_relevancy")}
    md.append("\nItens sem nota por falha do juiz (timeout/erro), por métrica: "
              + ", ".join(f"{m}: {v}/{len(items)}" for m, v in vazios.items())
              + ". Ficam fora da média.")
    md.append("\n`answer_relevancy` é o ponto fraco (mediana ~0,53) e mede **forma**, não correção: o Ragas "
              "gera perguntas a partir da resposta e compara os embeddings com a pergunta original. As respostas "
              "deste PoV trazem cabeçalho, tabela e o rodapé `**Fontes:**` exigidos pelo prompt, o que afasta o "
              "embedding da pergunta curta. Não interprete como taxa de acerto — `faithfulness` (~0,84) e o "
              "recall do retrieval são os indicadores de correção.")
    (REPORTS / "ragas.md").write_text("\n".join(md))
    print("\n".join(md))


if __name__ == "__main__":
    main()
