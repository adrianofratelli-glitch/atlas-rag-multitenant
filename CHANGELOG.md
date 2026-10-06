# Changelog

## Unreleased

## 1.3.0 (2026-10-06)

Hardening after an adversarial review.

- Fix: an upload named like a reference-corpus document, sent with "reindex", deleted the whole reference document (`ingest(reset=True)` deletes by `metadata.source`). The upload is now refused (422) and a TTL ingestion never deletes permanent chunks.
- `scripts/reset_demo.py`: indexes, corpus, embeddings (`autoEmbed`), cleanup of uploads, conversations and checkpoints, and a readiness probe, in one idempotent command. `ingest.py` and `setup_db.py` now refuse a database that does not end in `_test` unless `ALLOW_DEMO_DB_WRITE=1`.
- Resilience on by default: Claude retry before the first token (3 attempts), 120 s generation ceiling, Voyage retry on the classic path (3 attempts), prompt-injection filter on retrieved passages. Each variable only tunes or disables.
- Atlas not answering any retrieval path is a readable error instead of a "no evidence" refusal.
- LLM destination is always explicit (`llm_gateway.py`): Grove settings from `pov-shared`, or a declared `ANTHROPIC_BASE_URL`.
- LangGraph checkpoints expire (30 days) and turns without `thread_id` no longer share one null thread.
- Input hardening: 413 on oversized uploads from `Content-Length`, bounded upload read, source names capped at 200 characters, sanitised `X-Request-Id`.
- Docker image copies every root module (it was missing five and failed on import).
- `tests/test_hardening_adversarial.py` (32 tests). axios 1.20 (npm audit clean).

## 1.2.0 (2026-10-02)

Shows why one database is enough.

- Architecture drawing (`docs/architecture/one-database.svg`): a typical RAG stack (OLTP database, ETL/CDC, search engine, vector database, embedding and rerank APIs) next to this PoV, where embedding, lexical and vector search, fusion and rerank run in one aggregation on one Atlas cluster. In the README and the briefing.
- UI: a side panel beside the chat (screens 1280 px and wider) draws the same pipeline and follows each question. While the aggregation runs the candidates pulse in both branches; afterwards a 10 s loop replays it with that question's real sizes: 15 + 15 candidates, `$rankFusion`, `$rerank` over up to 30, and only 8 through `$limit`, coloured by the branch that found them. Before the first question it stays still. Narrow screens get a "ver arquitetura" modal instead.
- Native stats now carry the funnel sizes as sent in the pipeline (`branch_limit`, `rerank_candidates`, `final_n`).
- UI: the engine strip reads "1 aggregation no Atlas" on the native path, and per-turn latency is no longer shown on screen (still in the SSE `meta` event and `/api/metrics`).

## 1.1.0 (2026-10-02)

Retrieval moves into MongoDB Atlas 9.

- Native retrieval path (`RAG_NATIVE=1`): one aggregation runs `$rankFusion` over a vector branch (Automated Embedding, `voyage-4`) and a lexical branch (BM25), then `$rerank` (`rerank-3`). The app no longer generates vectors or calls a rerank API at query time. Generation is unchanged.
- Ingestion without client-side embeddings and without the embedding rate-limit pauses (`setup_db_native.py`, `EMBED_MODE`).
- Failures degrade instead of breaking the turn: a failing reranker or embedding model falls back to lexical-only and is flagged in the stats.
- Fix: every client that disconnected mid-stream leaked a chat concurrency slot, so four dropped connections left the API answering 429 until restart. The slot now follows the generating thread and generation stops once the client is gone.
- Fix: upload expiry was serialized without a time zone, so a 24 h TTL showed as 26 h at UTC-3.
- Fix: chunks found only by the lexical branch no longer show a 0% vector score.
- Refusal floor follows the active reranker (0.65 native, 0.6 classic), chosen on the calibration split and confirmed on the test split.
- UI: leaner engine strip, four starter questions, a hero that states the one-database/one-query architecture, and a friendly message when the concurrency limit is hit. Screenshots recaptured.
- UI: MongoDB 2026 "Dark Stage v4" layout (darker tokens, local Special Gothic / Source Code Pro, stair and grid motifs, staggered motion).
- Eval: `EVAL_TAG` and `EVAL_SOURCES` to compare configurations and corpora side by side. On the same 303 chunks, recall@1 went from 0.842 to 0.868 and nDCG@8 from 0.920 to 0.939; with reranking the gap is within noise, and the clearest gain is the embedding model.
- `autoEmbed` and `rerank-3` are previews in the MongoDB docs.

## 1.0.0 (2026-09-30)

First public release.

- Repository rebuilt with a clean, single-commit history.
- English README and repository description, with screenshots captured against a real Atlas cluster.
- MIT license.
- Internal notes, presentation decks, test-output snapshots, and tooling configuration removed from the repository.
