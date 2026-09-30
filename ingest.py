import os
import sys
import json
import time
import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
import voyageai
from config import DB_NAME, CLIENT_ID
from db import get_client
from dotenv import load_dotenv

load_dotenv()

SUPPORTED_FORMATS = {
    ".pdf", ".docx", ".doc", ".txt", ".csv",
    ".md", ".markdown", ".html", ".htm", ".json", ".xlsx", ".xls", ".pptx",
}


class JSONTextLoader:
    """One Document per top-level record (or the whole file if it's a single object)."""

    def __init__(self, file_path):
        self.file_path = file_path

    def load(self):
        data = json.loads(Path(self.file_path).read_text(encoding="utf-8"))
        records = data if isinstance(data, list) else [data]
        return [
            Document(
                page_content=json.dumps(r, ensure_ascii=False, indent=2),
                metadata={"page": i},
            )
            for i, r in enumerate(records)
        ]


class ExcelTextLoader:
    """One Document per worksheet, rendered as tab-separated rows."""

    def __init__(self, file_path):
        self.file_path = file_path

    def load(self):
        from openpyxl import load_workbook
        wb = load_workbook(self.file_path, data_only=True, read_only=True)
        docs = []
        for i, sheet in enumerate(wb.worksheets):
            lines = [
                "\t".join("" if c is None else str(c) for c in row)
                for row in sheet.iter_rows(values_only=True)
            ]
            docs.append(Document(
                page_content="\n".join(lines),
                metadata={"page": i, "sheet": sheet.title},
            ))
        return docs


class PowerPointTextLoader:
    """One Document per slide."""

    def __init__(self, file_path):
        self.file_path = file_path

    def load(self):
        from pptx import Presentation
        prs = Presentation(self.file_path)
        docs = []
        for i, slide in enumerate(prs.slides):
            texts = [
                shape.text for shape in slide.shapes
                if getattr(shape, "has_text_frame", False) and shape.text
            ]
            docs.append(Document(page_content="\n".join(texts), metadata={"page": i}))
        return docs


def get_loader(file_path: str):
    ext = Path(file_path).suffix.lower()
    if ext == ".pdf":
        from langchain_community.document_loaders import PyPDFLoader
        return PyPDFLoader(file_path)
    if ext in (".docx", ".doc"):
        from langchain_community.document_loaders import Docx2txtLoader
        return Docx2txtLoader(file_path)
    if ext in (".txt", ".md", ".markdown"):
        from langchain_community.document_loaders import TextLoader
        return TextLoader(file_path, encoding="utf-8")
    if ext == ".csv":
        from langchain_community.document_loaders import CSVLoader
        return CSVLoader(file_path)
    if ext in (".html", ".htm"):
        from langchain_community.document_loaders import BSHTMLLoader
        return BSHTMLLoader(file_path, open_encoding="utf-8")
    if ext == ".json":
        return JSONTextLoader(file_path)
    if ext in (".xlsx", ".xls"):
        return ExcelTextLoader(file_path)
    if ext == ".pptx":
        return PowerPointTextLoader(file_path)
    raise ValueError(
        f"Unsupported format: '{ext}'. "
        f"Accepted formats: {', '.join(sorted(SUPPORTED_FORMATS))}"
    )


class AlreadyIndexedError(RuntimeError):
    """Raised when the source already has chunks and reset was not requested."""

    def __init__(self, source_name: str, existing: int):
        self.source_name = source_name
        self.existing = existing
        super().__init__(
            f"'{source_name}' is already indexed ({existing} chunks). "
            "Use --reset to re-index."
        )


def ingest(
    file_path: str,
    reset: bool = False,
    nivel_acesso: str = "publico",
    source_name: str | None = None,
    on_progress=None,
    verbose: bool = True,
    ttl_hours: float | None = None,
) -> dict:
    """Index one file into the tenant's `documents` collection.

    Shared by the CLI and by the upload endpoint (`backend/documents.py`), which
    passes `on_progress(phase, done, total)` to drive the progress bar in the UI.

    ttl_hours stamps `metadata.expires_at` so the TTL index sweeps the chunks
    later — that's how demo uploads clean themselves up. Chunks ingested without
    it carry no such field, and MongoDB's TTL monitor ignores documents where the
    indexed field is missing, so the pre-loaded corpus never expires.

    Returns {"source", "chunks", "file", "expires_at"}.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    def log(message: str, end: str = "\n"):
        if verbose:
            print(message, end=end)

    def progress(phase: str, done: int, total: int):
        if on_progress:
            on_progress(phase, done, total)

    client = get_client()
    collection = client[DB_NAME]["documents"]

    source_name = source_name or path.stem
    existing = collection.count_documents({"metadata.source": source_name})

    if existing > 0 and not reset:
        raise AlreadyIndexedError(source_name, existing)

    if reset and existing > 0:
        log(f"Removing {existing} chunks from '{source_name}'...")
        collection.delete_many({"metadata.source": source_name})

    expires_at = None
    if ttl_hours and ttl_hours > 0:
        expires_at = datetime.now(timezone.utc) + timedelta(hours=ttl_hours)
        log(f"Chunks will expire at {expires_at.isoformat()} (TTL: {ttl_hours}h)")

    voyage = voyageai.Client(api_key=os.environ["VOYAGE_API_KEY"])

    log(f"Loading {path.name}...")
    progress("loading", 0, 0)
    loader = get_loader(file_path)
    docs = loader.load()
    log(f"   {len(docs)} pages/sections loaded")

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=800,
        chunk_overlap=150,
        separators=["\n\n", "\n", ".", " "],
    )
    chunks = splitter.split_documents(docs)
    log(f"   {len(chunks)} chunks generated")

    texts = [c.page_content for c in chunks]
    batch_size = 10  # conservative for the free tier (10K TPM)
    # Paid Voyage tiers allow much higher RPM — tune the pause without editing code.
    sleep_s = float(os.getenv("VOYAGE_SLEEP_S", "22"))
    inserted = 0

    log("Generating embeddings (free tier may take several minutes for large documents)...")
    progress("embedding", 0, len(texts))
    for i in range(0, len(texts), batch_size):
        batch_texts = texts[i : i + batch_size]
        batch_chunks = chunks[i : i + batch_size]

        result = voyage.embed(batch_texts, model="voyage-3", input_type="document")

        docs_to_insert = []
        for j, embedding in enumerate(result.embeddings):
            metadata = {
                "source": source_name,
                "client_id": CLIENT_ID,
                "file": path.name,
                "page": batch_chunks[j].metadata.get("page", 0),
                "chunk_id": i + j,
                # Access level used by the $vectorSearch / Atlas Search filter.
                "nivel_acesso": nivel_acesso,
            }
            # Only set when a TTL was asked for: the field's absence is what
            # keeps the pre-loaded corpus out of the TTL sweep.
            if expires_at:
                metadata["expires_at"] = expires_at
            docs_to_insert.append({
                "text": batch_chunks[j].page_content,
                "embedding": embedding,
                "metadata": metadata,
            })

        # Insert per batch: crash-safe (partial progress survives) and keeps
        # memory flat for large documents.
        collection.insert_many(docs_to_insert)
        inserted += len(docs_to_insert)

        done = min(i + batch_size, len(texts))
        log(f"   {done}/{len(texts)} chunks | batch {i // batch_size + 1}", end="\r")
        progress("embedding", done, len(texts))

        # Rate limit: free tier is 3 RPM (~20s between requests, with margin).
        if i + batch_size < len(texts):
            time.sleep(sleep_s)

    log(f"\nInserted {inserted} documents into Atlas (DB: {DB_NAME}).")
    log("Ingestion complete.")
    progress("done", inserted, inserted)
    return {
        "source": source_name,
        "chunks": inserted,
        "file": path.name,
        "expires_at": expires_at.isoformat() if expires_at else None,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Ingest documents into the RAG store "
        "(PDF, DOCX, TXT, CSV, Markdown, HTML, JSON, XLSX, PPTX)"
    )
    parser.add_argument(
        "file",
        nargs="?",
        default="data/documento.pdf",
        help="Path to the file to index",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Remove existing chunks and re-index the document",
    )
    parser.add_argument(
        "--ttl-horas",
        type=float,
        default=None,
        help="Expire this document's chunks after N hours (demo content). "
             "Omitted means the chunks are permanent.",
    )
    parser.add_argument(
        "--nivel",
        choices=["publico", "restrito"],
        default="publico",
        help="Access level (public/restricted) assigned to this document's chunks",
    )
    args = parser.parse_args()
    try:
        ingest(args.file, reset=args.reset, nivel_acesso=args.nivel, ttl_hours=args.ttl_horas)
    except FileNotFoundError as e:
        print(e)
        sys.exit(1)
    except AlreadyIndexedError as e:
        print(e)
