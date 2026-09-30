"""Utilidades do eval: carrega o golden e decide se um chunk recuperado contém a âncora."""
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
GOLDEN = ROOT / "eval/golden.jsonl"
REPORTS = ROOT / "eval/reports"


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<br>|\||\*\*|_", " ", s)).strip().lower()


def load_golden(kinds=None):
    rows = [json.loads(l) for l in open(GOLDEN, encoding="utf-8") if l.strip()]
    return [r for r in rows if not kinds or r["kind"] in kinds]


def anchor_pieces(anchor: str) -> list[str]:
    pieces = [anchor] + re.split(r"\||\.\.\.|;", anchor)
    out = []
    for p in pieces:
        p = norm(p)
        for n in (10_000, 70, 40):
            if len(p[:n]) > 10:
                out.append(p[:n])
    return list(dict.fromkeys(out))


def chunk_has_anchor(text: str, anchor: str) -> bool:
    t = norm(text)
    return any(p in t for p in anchor_pieces(anchor))
