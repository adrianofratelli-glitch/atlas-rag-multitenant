# Retrieval eval (sem LLM)

Golden: `eval/golden.jsonl` — 38 perguntas respondíveis (sintéticas). Relevância = chunk recuperado contém o trecho-âncora do documento. final_n=8.


## Pool de candidatos k=5

| config | n | erros | recall@1 | recall@3 | recall@5 | recall@8 | mrr | ndcg@8 |
|---|---|---|---|---|---|---|---|---|
| a_vector | 38 | 0 | 0.658 | 0.842 | 0.921 | 0.921 | 0.757 | 0.798 |
| b_vector+rerank | 38 | 0 | 0.816 | 0.921 | 0.921 | 0.921 | 0.868 | 0.882 |
| c_hybrid+rerank | 38 | 0 | 0.842 | 0.947 | 0.947 | 0.947 | 0.895 | 0.909 |
| d_hybrid_no_rerank | 38 | 0 | 0.711 | 0.921 | 0.947 | 0.947 | 0.814 | 0.848 |

## Pool de candidatos k=15

| config | n | erros | recall@1 | recall@3 | recall@5 | recall@8 | mrr | ndcg@8 |
|---|---|---|---|---|---|---|---|---|
| a_vector | 38 | 0 | 0.658 | 0.842 | 0.921 | 0.947 | 0.761 | 0.807 |
| b_vector+rerank | 38 | 0 | 0.842 | 0.947 | 0.974 | 0.974 | 0.901 | 0.920 |
| c_hybrid+rerank | 38 | 0 | 0.842 | 0.947 | 0.974 | 0.974 | 0.901 | 0.920 |
| d_hybrid_no_rerank | 38 | 0 | 0.684 | 0.895 | 0.921 | 0.974 | 0.791 | 0.836 |

## Pool de candidatos k=30

| config | n | erros | recall@1 | recall@3 | recall@5 | recall@8 | mrr | ndcg@8 |
|---|---|---|---|---|---|---|---|---|
| a_vector | 38 | 0 | 0.658 | 0.842 | 0.921 | 0.947 | 0.761 | 0.807 |
| b_vector+rerank | 38 | 0 | 0.868 | 0.974 | 1.000 | 1.000 | 0.928 | 0.946 |
| c_hybrid+rerank | 38 | 0 | 0.868 | 0.974 | 1.000 | 1.000 | 0.928 | 0.946 |
| d_hybrid_no_rerank | 38 | 0 | 0.684 | 0.868 | 0.921 | 0.947 | 0.782 | 0.823 |

## Latência média de retrieval (ms, k=5)

O embedding da pergunta é cacheado entre modos: o primeiro modo executado (a_vector) paga a chamada à Voyage, os demais não. Compare só a diferença entre com e sem rerank/léxico, não o valor absoluto de `a`.

| modo | ms |
|---|---|
| a_vector | 756 |
| b_vector+rerank | 724 |
| c_hybrid+rerank | 775 |
| d_hybrid_no_rerank | 446 |

Rerank degradado (fallback RRF) em 0 chamadas.


## Piso da recusa (`RAG_MIN_RERANK_SCORE`): score máximo do rerank-2 por tipo

O piso é **escolhido no split `calib`** e conferido no split `test`, que é o usado pelo relatório de recusa. As duas tabelas abaixo devem contar a mesma história; se divergirem muito, o piso está sobreajustado ao `calib`.

### Split `calib` (escolha do piso)

| tipo | n | min | mediana | max |
|---|---|---|---|---|
| answerable | 19 | 0.641 | 0.898 | 0.969 |
| no_answer | 3 | 0.578 | 0.633 | 0.668 |
| cross_tenant | 3 | 0.215 | 0.273 | 0.297 |

| piso | respondíveis recusadas (falso-recusa) | sem-resposta+cross-tenant recusadas |
|---|---|---|
| 0.3 | 0/19 | 3/6 |
| 0.5 | 0/19 | 3/6 |
| 0.6 | 0/19 | 4/6 |
| 0.65 | 1/19 | 5/6 |
| 0.7 | 2/19 | 6/6 |

### Split `test` (verificação; é o que o relatório de recusa mede)

| tipo | n | min | mediana | max |
|---|---|---|---|---|
| answerable | 19 | 0.762 | 0.906 | 0.977 |
| no_answer | 2 | 0.582 | 0.609 | 0.609 |
| cross_tenant | 2 | 0.226 | 0.566 | 0.566 |

| piso | respondíveis recusadas (falso-recusa) | sem-resposta+cross-tenant recusadas |
|---|---|---|
| 0.3 | 0/19 | 1/4 |
| 0.5 | 0/19 | 1/4 |
| 0.6 | 0/19 | 3/4 |
| 0.65 | 0/19 | 4/4 |
| 0.7 | 0/19 | 4/4 |

Maior piso sem falsa recusa no `calib`: **0.6** (4/6 recusas devidas). É o valor sugerido para `RAG_MIN_RERANK_SCORE`.
