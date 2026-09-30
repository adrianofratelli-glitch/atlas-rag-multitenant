# Taxa de recusa (split `all`, gate por score = off, piso 0.6)

Recusa = gate do backend (fora de escopo / sem evidência) **ou** veredito de um juiz LLM sobre a resposta gerada. A regex de recusa é só pré-filtro; quem decide é o juiz (`eval/refusal_judge.py`).

| tipo | esperado | correto | n |
|---|---|---|---|
| no_answer | recusar | 5 (100%) | 5 |
| cross_tenant | recusar | 5 (100%) | 5 |
| answerable | responder | 38 (100%) | 38 |

Origem do veredito: {'judge': 48}. Itens em que o juiz discordou da regex: 0 (—).

**Limitações.** O juiz é Claude, o mesmo modelo que gera as respostas, então os dois erram junto em recusas ambíguas — só rótulo humano fecharia isso. Quando o juiz está indisponível, o veredito cai para a regex (visível em `Origem do veredito`), que só é conclusiva quando casa. As perguntas são sintéticas (escritas por Claude a partir do próprio documento).
