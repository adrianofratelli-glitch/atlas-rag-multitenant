# Taxa de recusa (split `test`, gate por score = on, piso 0.6)

Recusa = gate do backend (fora de escopo / sem evidência) **ou** veredito de um juiz LLM sobre a resposta gerada. A regex de recusa é só pré-filtro; quem decide é o juiz (`eval/refusal_judge.py`).

| tipo | esperado | correto | n |
|---|---|---|---|
| no_answer | recusar | 2 (100%) | 2 |
| cross_tenant | recusar | 2 (100%) | 2 |
| answerable | responder | 18 (95%) | 19 |

Gate do backend disparou em 3 item(ns): q42/no_answer, q45/cross_tenant, q47/cross_tenant.
Respondíveis recusadas **pelo gate** (custo direto do piso): 0 (—).
Respondíveis recusadas **pelo juiz**, sem o gate ter disparado: 1 (q16) — aqui o modelo respondeu e o juiz considerou que não entregou o pedido; não é efeito do piso.
Origem do veredito: {'judge': 20, 'gate:no_evidence': 3}. Itens em que o juiz discordou da regex: 1 (q16).

**Limitações.** O juiz é Claude, o mesmo modelo que gera as respostas, então os dois erram junto em recusas ambíguas — só rótulo humano fecharia isso. Quando o juiz está indisponível, o veredito cai para a regex (visível em `Origem do veredito`), que só é conclusiva quando casa. As perguntas são sintéticas (escritas por Claude a partir do próprio documento).
