#!/bin/sh
set -e

cd /app
# One worker only: job/status/outline caches in backend/documents.py and
# backend/api.py are in-process dicts, not shared across workers. With 2+
# workers, nginx round-robins requests and a job created on worker A can 404
# when polled on worker B (~50% chance) while ingestion is still running for
# real. The real bottleneck is the VoyageAI/Anthropic APIs, not CPU, so one
# worker is not a throughput regression — it removes a real bug at the root.
# See the README notes on workers and in-memory state for the not-yet-implemented
# alternative if 2+ workers are ever needed.
uvicorn backend.api:app --host 127.0.0.1 --port 8000 --workers 1 &
nginx -g 'daemon off;'
