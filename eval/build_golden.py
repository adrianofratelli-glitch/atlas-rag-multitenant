"""Monta eval/golden.jsonl a partir de eval/questions.json + o corpus em data/.

Agnóstico de tenant: as perguntas, os gabaritos e os trechos-âncora vivem em
`eval/questions.json`, que fica **fora do git** (como `client_config.json` e `data/`),
porque citam o documento real. Copie `eval/questions.example.json` e preencha.

A página de cada pergunta é derivada do rodapé numerado do corpus, procurando o
trecho-âncora no texto normalizado — por isso o golden não guarda `_id` de chunk,
que muda a cada reingestão.

  python eval/build_golden.py [--corpus data/documento.md] [--questions eval/questions.json]
"""
import argparse
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

norm = lambda s: re.sub(r"\s+", " ", re.sub(r"<br>|\||\*\*", " ", s)).strip()


def find_corpus(explicit: str | None) -> pathlib.Path:
    if explicit:
        return pathlib.Path(explicit)
    candidates = sorted((ROOT / "data").glob("*.md"))
    if len(candidates) != 1:
        sys.exit(f"informe --corpus: esperava 1 .md em data/, encontrei {len(candidates)}")
    return candidates[0]


def page_finder(text: str, footer_rx: str):
    """Devolve page_of(anchor) -> número da página impressa, ou None.

    `footer_rx` casa o rodapé que precede o número da página; sem rodapé numerado
    (capa, elementos pré-textuais, última página) a página fica None.
    """
    body = norm(text)
    rx = re.compile(footer_rx + r"\s*(\d{1,3})\s")
    numbered = rx.findall(body)
    first_numbered = rx.search(body)

    def page_of(anchor: str):
        pieces = [anchor] + re.split(r"\||\.\.\.|;", anchor)
        cands = [norm(x)[:n] for x in pieces for n in (10_000, 70, 40)]
        i = next((body.find(c) for c in cands if len(c) > 10 and c in body), -1)
        if i < 0:
            sys.exit(f"âncora não encontrada no corpus: {anchor[:60]!r}")
        if first_numbered and i < first_numbered.start():
            return None  # antes do primeiro rodapé numerado
        m = rx.search(body, i)
        if m:
            return int(m.group(1))
        return int(numbered[-1]) + 1 if numbered else None  # após o último rodapé

    return page_of


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", help="markdown do corpus (default: o único .md em data/)")
    ap.add_argument("--questions", default=str(ROOT / "eval/questions.json"))
    ap.add_argument("--out", default=str(ROOT / "eval/golden.jsonl"))
    a = ap.parse_args()

    qpath = pathlib.Path(a.questions)
    if not qpath.exists():
        sys.exit(f"{qpath} não existe — copie eval/questions.example.json e preencha.")
    spec = json.loads(qpath.read_text(encoding="utf-8"))
    corpus = find_corpus(a.corpus)
    page_of = page_finder(corpus.read_text(encoding="utf-8"),
                          spec.get("page_footer_regex", r"(?:p[áa]gina|p\.)"))

    rows = []
    for q in spec.get("answerable", []):
        rows.append(dict(question=q["question"], reference=q["reference"], anchor=q["anchor"], kind="answerable"))
    for q in spec.get("no_answer", []):
        rows.append(dict(question=q, reference=None, anchor=None, kind="no_answer"))
    for q in spec.get("cross_tenant", []):
        rows.append(dict(question=q, reference=None, anchor=None, kind="cross_tenant"))
    if not rows:
        sys.exit(f"{qpath} não tem perguntas.")

    # Split determinístico por posição, estável entre execuções: `calib` calibra o piso da
    # recusa, `test` produz os números do relatório. Sem isso o piso seria escolhido no mesmo
    # conjunto que o avalia, e a taxa de recusa sairia otimista.
    with open(a.out, "w", encoding="utf-8") as f:
        seen = {}
        for i, r in enumerate(rows, 1):
            n = seen[r["kind"]] = seen.get(r["kind"], 0) + 1
            r["id"] = f"q{i:02d}"
            r["synthetic"] = True
            r["split"] = "calib" if n % 2 else "test"  # alterna dentro de cada tipo
            r["page"] = page_of(r["anchor"]) if r["anchor"] else None
            r["should_refuse"] = r["kind"] != "answerable"
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"{len(rows)} linhas -> {a.out} (corpus: {corpus})")


if __name__ == "__main__":
    main()
