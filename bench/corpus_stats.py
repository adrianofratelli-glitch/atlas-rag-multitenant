"""Collect side-by-side corpus statistics for the two ingestion paths."""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langchain_community.document_loaders import PyPDFLoader  # noqa: E402
from db import get_client  # noqa: E402

# Agnóstico de tenant: os caminhos e os bancos do comparativo vêm do ambiente.
# BENCH_PDF/BENCH_MD são as duas extrações do MESMO documento; BENCH_DB_PDF/BENCH_DB_MD
# os bancos em que cada uma foi indexada.
PDF = os.environ["BENCH_PDF"]
MD = os.environ["BENCH_MD"]
DB_PDF = os.environ["BENCH_DB_PDF"]
DB_MD = os.environ["BENCH_DB_MD"]


def md_stats(text: str) -> dict:
    lines = text.splitlines()
    return {
        "headings": sum(1 for line in lines if line.lstrip().startswith("#")),
        "table_rows": sum(1 for line in lines if line.strip().startswith("|")),
    }


def main() -> None:
    pdf_text = "\n".join(d.page_content for d in PyPDFLoader(PDF).load())
    md_text = Path(MD).read_text(encoding="utf-8")
    client = get_client()

    stats = {
        "pdf": {
            "file": Path(PDF).name,
            "loader": "PyPDFLoader",
            "chars": f"{len(pdf_text):,}".replace(",", "."),
            "chunks": client[DB_PDF]["documents"].count_documents({}),
            **md_stats(pdf_text),
            "pages": "sim (nº real da página)",
        },
        "md": {
            "file": Path(MD).name,
            "loader": "TextLoader",
            "chars": f"{len(md_text):,}".replace(",", "."),
            "chunks": client[DB_MD]["documents"].count_documents({}),
            **md_stats(md_text),
            "pages": "não (arquivo único, page=0)",
        },
    }
    out = Path(__file__).parent / "corpus_stats.json"
    out.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
