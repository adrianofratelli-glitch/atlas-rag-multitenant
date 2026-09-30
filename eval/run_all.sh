#!/usr/bin/env bash
# Reprodução completa do eval. Dois venvs por conflito de dependências (dependency conflicts).
#   ./eval/run_all.sh [--refresh]     # --refresh ignora o cache de retrieval e refaz as chamadas ao Atlas/Voyage
# Requer .env com MONGO_URI, VOYAGE_API_KEY e as credenciais do Claude.
set -euo pipefail
cd "$(dirname "$0")/.."

[ -d .venv-eval ] || { python3 -m venv .venv-eval && .venv-eval/bin/pip install -q -e "../_shared[llm,eval,guardrails]"; }

.venv/bin/python eval/build_golden.py                        # golden.jsonl a partir de eval/questions.json + data/
.venv/bin/python -m eval.run_retrieval --ks 5,15,30 "$@"     # recall@k / MRR / nDCG + calibração da recusa
.venv/bin/python -m eval.run_generation --gate off --split test   # taxa de recusa no split de teste
.venv/bin/python -m eval.run_generation --gate on  --split test   # idem, com RAG_REFUSE_WEAK_EVIDENCE
.venv/bin/python -m eval.run_generation --gate off --split all    # respostas do golden inteiro (entrada do Ragas)
.venv-eval/bin/python -m eval.run_ragas                      # faithfulness / context precision / answer relevancy

# Para reescrever um relatório de recusa sem gerar de novo (não chama LLM):
#   .venv/bin/python -m eval.report_refusal eval/reports/answers_on_test.jsonl
echo "Relatórios em eval/reports/: retrieval.md, refusal_off_test.md, refusal_on_test.md, ragas.md"
