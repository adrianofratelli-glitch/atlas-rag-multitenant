"""Renderiza eval/reports/refusal_<gate>_<split>.md a partir de um answers_*.jsonl já gerado.

Separado de run_generation porque não chama LLM nenhum: dá para reescrever ou corrigir o
relatório sem pagar de novo pela geração e pelo juiz.

  python -m eval.report_refusal eval/reports/answers_on_test.jsonl
"""
import argparse
import collections
import json
import pathlib
import sys

sys.path.insert(0, ".")

from eval.common import REPORTS  # noqa: E402


def render(rows: list[dict], *, gate: str, split: str, threshold) -> str:
    md = [f"# Taxa de recusa (split `{split}`, gate por score = {gate}, piso {threshold})\n",
          "Recusa = gate do backend (fora de escopo / sem evidência) **ou** veredito de um juiz LLM sobre a "
          "resposta gerada. A regex de recusa é só pré-filtro; quem decide é o juiz (`eval/refusal_judge.py`).\n",
          "| tipo | esperado | correto | n |", "|---|---|---|---|"]
    for kind, want, label in (("no_answer", True, "recusar"), ("cross_tenant", True, "recusar"),
                              ("answerable", False, "responder")):
        sub = [x for x in rows if x["kind"] == kind]
        ok = sum(1 for x in sub if x["refused"] == want)
        md.append(f"| {kind} | {label} | {ok} ({ok / max(1, len(sub)):.0%}) | {len(sub)} |")

    # De onde veio cada recusa: o gate (decisão do código, auditável) ou o juiz (julgamento do
    # texto gerado). Só a primeira é atribuível ao piso de `RAG_MIN_RERANK_SCORE`.
    gate_fired = [x for x in rows if x["gate"]]
    gate_wrong = [x["id"] for x in gate_fired if x["kind"] == "answerable"]
    judge_wrong = [x["id"] for x in rows if x["kind"] == "answerable" and x["refused"] and not x["gate"]]
    src = collections.Counter(x["verdict"]["source"] for x in rows)
    disagree = [x["id"] for x in rows
                if x["verdict"]["judge"] is not None and x["verdict"]["judge"] != x["verdict"]["regex"]]
    md += ["",
           f"Gate do backend disparou em {len(gate_fired)} item(ns): "
           f"{', '.join(f'{x['id']}/{x['kind']}' for x in gate_fired) or '—'}.",
           f"Respondíveis recusadas **pelo gate** (custo direto do piso): {len(gate_wrong)} "
           f"({', '.join(gate_wrong) if gate_wrong else '—'}).",
           f"Respondíveis recusadas **pelo juiz**, sem o gate ter disparado: {len(judge_wrong)} "
           f"({', '.join(judge_wrong) if judge_wrong else '—'}) — aqui o modelo respondeu e o juiz "
           f"considerou que não entregou o pedido; não é efeito do piso.",
           f"Origem do veredito: {dict(src)}. Itens em que o juiz discordou da regex: "
           f"{len(disagree)} ({', '.join(disagree) if disagree else '—'}).",
           "",
           "**Limitações.** O juiz é Claude, o mesmo modelo que gera as respostas, então os dois erram junto "
           "em recusas ambíguas — só rótulo humano fecharia isso. Quando o juiz está indisponível, o veredito "
           "cai para a regex (visível em `Origem do veredito`), que só é conclusiva quando casa. As perguntas "
           "são sintéticas (escritas por Claude a partir do próprio documento)."]
    return "\n".join(md) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("answers", help="eval/reports/answers_<gate>_<split>.jsonl")
    a = ap.parse_args()
    path = pathlib.Path(a.answers)
    gate, split = path.stem.split("_")[1:3]
    rows = [json.loads(l) for l in path.open(encoding="utf-8") if l.strip()]
    import agent
    out = REPORTS / f"refusal_{gate}_{split}.md"
    out.write_text(render(rows, gate=gate, split=split, threshold=agent.MIN_RERANK_SCORE))
    print(out.read_text())


if __name__ == "__main__":
    main()
