"""Rótulo humano para o eval do Graph-RAG: planilha para preencher e concordância com o juiz LLM.

    python eval/human_review.py export          # gera eval/reports/human_review.xlsx
    # preencha as colunas amarelas (aba "auditoria": sim/parcial/nao; aba "respostas": A/B/empate)
    python eval/human_review.py score           # lê a planilha e grava eval/reports/human_agreement.md

Fontes: `graph_audit.jsonl` (itens do grafo + trecho) e `graph_compare.jsonl` (respostas do eval).
O veredito do juiz não aparece na planilha: as respostas vêm com rótulo A/B sorteado de novo, e o
mapeamento fica numa aba oculta. Assim o rótulo humano não é contaminado pelo do modelo, e a
concordância mede o quanto o juiz LLM (o mesmo modelo que gera) merece confiança.
Planilha e relatório ficam fora do git (citam o corpus).
"""
import argparse
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "eval" / "reports"
XLSX = REPORTS / "human_review.xlsx"


def _rows(path):
    p = REPORTS / path
    if not p.exists():
        sys.exit(f"faltando {p}: rode o eval/auditoria antes")
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


def _chunk_texts(ids):
    sys.path.insert(0, str(ROOT))
    from bson import ObjectId
    from backend.config import CLIENT_ID, DB_NAME
    from backend.db import get_client

    coll = get_client()[DB_NAME]["documents"]
    out = {}
    for cid in ids:
        try:
            doc = coll.find_one({"_id": ObjectId(cid), "metadata.client_id": CLIENT_ID}, {"text": 1})
        except Exception:  # noqa: BLE001
            doc = None
        out[cid] = (doc or {}).get("text", "(trecho não encontrado: o corpus foi reingerido?)")
    return out


def export(seed: int) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.worksheet.datavalidation import DataValidation

    audit = _rows("graph_audit.jsonl")
    compare = _rows("graph_compare.jsonl")
    texts = _chunk_texts({r["chunk_id"] for r in audit})
    wb = Workbook()
    fill = PatternFill("solid", fgColor="FFF2CC")
    wrap = Alignment(wrap_text=True, vertical="top")

    ws = wb.active
    ws.title = "auditoria"
    ws.append(["n", "tipo", "item", "trecho de origem", "o trecho sustenta o item? (sim/parcial/nao)", "comentário"])
    for i, r in enumerate(audit, 1):
        item = (f"{r['label']} [{r['type']}]: {r.get('description', '')}" if "label" in r
                else f"{r['source']} --{r['type']}--> {r['target']}")
        ws.append([i, "entidade" if "label" in r else "relação", item, texts.get(r["chunk_id"], "")[:3000], "", ""])
    dv = DataValidation(type="list", formula1='"sim,parcial,nao"', allow_blank=True)
    ws.add_data_validation(dv)
    dv.add(f"E2:E{len(audit) + 1}")

    ws2 = wb.create_sheet("respostas")
    ws2.append(["n", "pergunta", "resposta A", "resposta B", "qual atende melhor? (A/B/empate)", "comentário"])
    key = wb.create_sheet("_chave")
    key.append(["n", "id", "par", "A", "B"])
    rng = random.Random(seed)
    n = 0
    for row in compare:
        for rival in ("hybrid", "hybrid_wide"):
            n += 1
            pair = ["graph", rival]
            rng.shuffle(pair)
            ws2.append([n, row["question"], row["results"][pair[0]]["answer"], row["results"][pair[1]]["answer"], "", ""])
            key.append([n, row["id"], rival, pair[0], pair[1]])
    dv2 = DataValidation(type="list", formula1='"A,B,empate"', allow_blank=True)
    ws2.add_data_validation(dv2)
    dv2.add(f"E2:E{n + 1}")
    key.sheet_state = "hidden"

    for sheet, widths in ((ws, [5, 10, 60, 90, 22, 30]), (ws2, [5, 40, 80, 80, 22, 30])):
        for col, w in zip("ABCDEF", widths):
            sheet.column_dimensions[col].width = w
        for cells in sheet.iter_rows(min_row=2):
            for c in cells:
                c.alignment = wrap
            cells[4].fill = fill
        for c in sheet[1]:
            c.font = Font(bold=True)
        sheet.freeze_panes = "A2"
    REPORTS.mkdir(parents=True, exist_ok=True)
    wb.save(XLSX)
    print(f"planilha: {XLSX} ({len(audit)} itens de auditoria, {n} pares de respostas)")


def _kappa(pairs, labels):
    if not pairs:
        return None
    agree = sum(1 for a, b in pairs if a == b) / len(pairs)
    pa = {k: sum(1 for a, _ in pairs if a == k) / len(pairs) for k in labels}
    pb = {k: sum(1 for _, b in pairs if b == k) / len(pairs) for k in labels}
    expected = sum(pa[k] * pb[k] for k in labels)
    return agree, (agree - expected) / (1 - expected) if expected < 1 else 1.0


def score() -> None:
    from openpyxl import load_workbook

    wb = load_workbook(XLSX)
    audit = _rows("graph_audit.jsonl")
    compare = {r["id"]: r for r in _rows("graph_compare.jsonl")}
    a_pairs = []
    for row in wb["auditoria"].iter_rows(min_row=2, values_only=True):
        n, human = row[0], (row[4] or "").strip().lower()
        if human in ("sim", "parcial", "nao", "não") and audit[n - 1].get("suporte"):
            a_pairs.append((human.replace("não", "nao"), audit[n - 1]["suporte"]))
    key = {r[0]: r for r in wb["_chave"].iter_rows(min_row=2, values_only=True)}
    r_pairs = []
    for row in wb["respostas"].iter_rows(min_row=2, values_only=True):
        n, human = row[0], (row[4] or "").strip()
        if human not in ("A", "B", "empate"):
            continue
        _, qid, rival, a, b = key[n]
        human_winner = "empate" if human == "empate" else (a if human == "A" else b)
        judge_winner = compare[qid]["pairs"][rival]["winner"]
        if judge_winner:
            r_pairs.append((human_winner, judge_winner))
    lines = ["# Concordância humano × juiz LLM", ""]
    for label, pairs, labels in (("auditoria do grafo", a_pairs, ["sim", "parcial", "nao"]),
                                 ("respostas (qual atende melhor)", r_pairs, None)):
        labels = labels or sorted({x for p in pairs for x in p})
        res = _kappa(pairs, labels)
        if res is None:
            lines.append(f"- {label}: nenhum item rotulado.")
            continue
        agree, kappa = res
        lines.append(f"- {label}: {len(pairs)} itens rotulados, concordância {agree:.0%}, kappa de Cohen {kappa:.2f}.")
        if label.startswith("auditoria"):
            human_ok = sum(1 for h, _ in pairs if h == "sim") / len(pairs)
            judge_ok = sum(1 for _, j in pairs if j == "sim") / len(pairs)
            lines.append(f"  - sustentado segundo o humano: {human_ok:.0%} (juiz: {judge_ok:.0%})")
        else:
            graph_h = sum(1 for h, _ in pairs if h == "graph") / len(pairs)
            lines.append(f"  - grafo melhor segundo o humano: {graph_h:.0%} dos pares")
    lines += ["", "Kappa acima de 0,6 = concordância substancial; abaixo de 0,4, o juiz LLM não substitui o rótulo humano."]
    (REPORTS / "human_agreement.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cmd", choices=["export", "score"])
    ap.add_argument("--seed", type=int, default=5)
    args = ap.parse_args(argv)
    export(args.seed) if args.cmd == "export" else score()
    return 0


if __name__ == "__main__":
    sys.exit(main())
