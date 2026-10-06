"""Recria o estado da demo do tenant num comando: índices, corpus base, embeddings e limpeza.

    ALLOW_DEMO_DB_WRITE=1 python scripts/reset_demo.py            # corpus de DEMO_CORPUS ou data/
    python scripts/reset_demo.py data/a.md data/b.pdf             # num banco *_test, sem a flag
    python scripts/reset_demo.py --dry-run                        # só mostra o que faria

Idempotente: rodar duas vezes deixa o mesmo estado. Passos:
  1. coleções + índices (vector_index, text_index, TTLs): `setup_db_native` com RAG_NATIVE=1,
     `setup_db` no caminho clássico. Só cria o que falta.
  2. reingere cada arquivo do corpus com reset (por `metadata.source`). No nativo o Atlas gera os
     embeddings (`autoEmbed`); no clássico o `ingest.py` chama a Voyage.
  3. remove o que não é corpus: uploads (chunks com `metadata.expires_at`) e fontes fora da lista.
  4. limpa conversas e checkpoints do LangGraph do tenant (`--keep-conversations` preserva).
  5. espera os índices responderem: uma consulta do caminho real precisa devolver trechos.

Grava no banco, então passa pela mesma guarda dos scripts de setup: banco `*_test`, ou
ALLOW_DEMO_DB_WRITE=1 de propósito. A identidade do tenant em `_meta` é preservada.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)  # config.py lê client_config.json e o .env relativos à raiz

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from config import CLIENT_ID, DB_NAME, NATIVE_ENABLED, assert_writable_db  # noqa: E402

PROBE_QUERY = os.getenv("RESET_PROBE_QUERY", "objetivos")


def corpus_files(args_files: list[str]) -> list[Path]:
    """Arquivos do corpus base: argumentos > DEMO_CORPUS (vírgulas) > data/ (nível de cima)."""
    from ingest import SUPPORTED_FORMATS

    if args_files:
        files = [Path(f) for f in args_files]
    elif os.getenv("DEMO_CORPUS"):
        files = [Path(f.strip()) for f in os.environ["DEMO_CORPUS"].split(",") if f.strip()]
    else:
        files = sorted(p for p in (ROOT / "data").iterdir()
                       if p.is_file() and p.suffix.lower() in SUPPORTED_FORMATS)
    missing = [str(f) for f in files if not f.exists()]
    if missing:
        raise SystemExit(f"arquivo(s) do corpus não encontrado(s): {', '.join(missing)}")
    if not files:
        raise SystemExit("nenhum arquivo de corpus: passe os caminhos ou defina DEMO_CORPUS")
    stems: dict[str, Path] = {}
    for f in files:
        if f.stem in stems:
            raise SystemExit(
                f"'{f.name}' e '{stems[f.stem].name}' viram a mesma fonte '{f.stem}'. "
                "Defina DEMO_CORPUS com a lista exata do corpus.")
        stems[f.stem] = f
    return files


def wait_until_searchable(timeout_s: float) -> dict:
    """Consulta pelo caminho real até voltar trecho (índices prontos e embeddings em dia)."""
    from agent import retrieve_context

    deadline = time.monotonic() + timeout_s
    last: dict = {}
    while True:
        try:
            _, sources, stats = retrieve_context(PROBE_QUERY, access_levels=["publico", "restrito"])
            last = {"hits": len(sources), "degraded": bool(stats.get("native_degraded")
                                                             or stats.get("rerank_degraded"))}
            if sources and not last["degraded"]:
                return last
        except Exception as exc:  # noqa: BLE001 — índice ainda subindo
            last = {"hits": 0, "error": type(exc).__name__}
        if time.monotonic() > deadline:
            return {**last, "timeout": True}
        time.sleep(10)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("files", nargs="*", help="arquivos do corpus base (padrão: DEMO_CORPUS ou data/)")
    parser.add_argument("--keep-conversations", action="store_true",
                        help="não apaga conversas nem checkpoints do LangGraph")
    parser.add_argument("--dry-run", action="store_true", help="mostra o plano e não grava nada")
    parser.add_argument("--wait-s", type=float, default=300, help="espera máxima pelos índices (0 = não espera)")
    args = parser.parse_args()

    files = corpus_files(args.files)
    keep = {f.stem for f in files}
    mode = "nativo (autoEmbed)" if NATIVE_ENABLED else "clássico (voyage-3)"
    print(f"banco: {DB_NAME} · tenant: {CLIENT_ID} · caminho: {mode}")
    print("corpus: " + ", ".join(f.name for f in files))
    if args.dry_run:
        print("dry-run: nada gravado")
        return 0
    assert_writable_db(DB_NAME)

    t0 = time.perf_counter()
    if NATIVE_ENABLED:
        import setup_db_native as setup_mod
    else:
        import setup_db as setup_mod
    setup_mod.setup()

    from db import get_client, verify_tenant_identity
    from ingest import ingest

    client = get_client()
    verify_tenant_identity(DB_NAME, CLIENT_ID)
    docs = client[DB_NAME]["documents"]

    summary: dict[str, int] = {}
    for f in files:
        result = ingest(str(f), reset=True, verbose=False)
        summary[result["source"]] = result["chunks"]
        print(f"  {result['source']}: {result['chunks']} chunks")

    uploads = docs.delete_many({"metadata.expires_at": {"$exists": True}}).deleted_count
    strays = docs.delete_many({"metadata.source": {"$nin": sorted(keep)}}).deleted_count
    print(f"removidos: {uploads} chunks de upload, {strays} chunks de fontes fora do corpus")

    if not args.keep_conversations:
        from rag_graph import checkpoint_db_name

        conv = client[DB_NAME]["conversations"].delete_many({}).deleted_count
        ck_db = client[checkpoint_db_name()]
        ck = ck_db["langgraph_checkpoints"].delete_many({}).deleted_count
        ckw = ck_db["langgraph_checkpoint_writes"].delete_many({}).deleted_count
        print(f"limpos: {conv} conversas, {ck} checkpoints, {ckw} checkpoint writes")

    total = docs.count_documents({})
    print(f"chunks no banco: {total} (esperado {sum(summary.values())})")
    if args.wait_s > 0:
        print(f"aguardando os índices responderem (até {int(args.wait_s)} s)...")
        probe = wait_until_searchable(args.wait_s)
        print(f"consulta de verificação '{PROBE_QUERY}': {probe}")
        if probe.get("timeout"):
            print("AVISO: índices ainda não responderam; rode de novo ou espere mais um pouco.")
            return 2
    print(f"reset concluído em {time.perf_counter() - t0:.0f} s")
    return 0 if total == sum(summary.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
