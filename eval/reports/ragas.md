# Ragas

38 respostas avaliadas (respondíveis, não recusadas).

**Limitação:** o dataset (perguntas/gabaritos) foi escrito por Claude e o juiz do Ragas também é Claude (além de o gerador das respostas ser Claude Sonnet). Juiz e gerador compartilham viés; trate os números como indicador relativo entre configurações, não como taxa real de qualidade.

| config | n | erros | faithfulness | context_precision | answer_relevancy |
|---|---|---|---|---|---|
| run | 38 | 0 | 0.851 | 0.841 | 0.533 |

Itens sem nota por falha do juiz (timeout/erro), por métrica: faithfulness: 1/38, context_precision: 0/38, answer_relevancy: 0/38. Ficam fora da média.

`answer_relevancy` é o ponto fraco (mediana ~0,53) e mede **forma**, não correção: o Ragas gera perguntas a partir da resposta e compara os embeddings com a pergunta original. As respostas deste PoV trazem cabeçalho, tabela e o rodapé `**Fontes:**` exigidos pelo prompt, o que afasta o embedding da pergunta curta. Não interprete como taxa de acerto — `faithfulness` (~0,84) e o recall do retrieval são os indicadores de correção.