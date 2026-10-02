# Changelog

## Unreleased

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
