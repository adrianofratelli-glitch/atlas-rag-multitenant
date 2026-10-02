# Interface, fluxos e roteiro de demo

> Onde cada tela/componente vive, o contrato de streaming, e o roteiro de 7 passos para apresentar a PoV. Pipeline de recuperação em detalhe: `queries.md`. Arquitetura geral: `architecture.md`.

## O papel da tela

O ponto da PoV é o pipeline de recuperação híbrida. Mas ninguém aprova um pipeline olhando log — a tela é onde o híbrido, o rerank e o controle de acesso viram argumento visível.

Regra de design: **a UI não decide nada de recuperação.** Ela só mostra o que o backend já resolveu (escopo, ACL, fontes).

## Stack de frontend

| Item | Escolha | Motivo |
|---|---|---|
| Build | Vite, porta `5180` fixa (`strictPort`) | evita colisão com outras PoVs na mesma máquina; falha alto em vez de trocar de porta em silêncio |
| UI kit | LeafyGreen (design system MongoDB) | a demo já parece produto Atlas |
| Estado | `useState` em `App.jsx` (casca) e em `WorkspaceView.jsx` (cada aba) | poucos estados, dois componentes de orquestração — não justifica lib de estado |
| HTTP | `axios` nos GETs, `fetch` cru no chat (`frontend/src/api.js`) | streaming precisa de `ReadableStream`, que `axios` não entrega no browser |
| Markdown | `react-markdown` + `remark-gfm` | resposta do Claude vem em Markdown, com tabela e lista |

Em dev, o Vite faz proxy de `/api` → `:8180`. Em produção (Docker), o nginx faz esse mesmo papel, mesma origem, sem CORS.

## Estrutura de telas — duas abas, sem router

Uma tela só (sem sidebar, sem roteamento), dividida em **duas abas que nunca se misturam**:

- **`Corpus de referência`** (`tab: 'base'`) — o documento do tenant, só leitura.
- **`Novo conteúdo`** (`tab: 'uploads'`) — o que for enviado durante a demo.

`App.jsx` monta **as duas** ao mesmo tempo (`hidden` no `role="tabpanel"` da inativa) — de propósito: trocar de aba não pode matar um stream em andamento nem apagar a conversa da outra aba. "Nova conversa" (`newChat()`) remonta só a aba ativa via mudança de `key` em `WorkspaceView`, zerando thread/histórico/documentos marcados daquela aba.

## Componentes

| Componente | Arquivo | Papel |
|---|---|---|
| `App` | `frontend/src/App.jsx` | casca: config, status do Atlas, as duas abas, nível de acesso, tela offline |
| `WorkspaceView` | `frontend/src/components/WorkspaceView.jsx` | um espaço de trabalho isolado: `threadId` próprio (UUID gerado no cliente), mensagens, documentos marcados (`sources`), `scope` fixo |
| `TopBar` | `frontend/src/components/TopBar.jsx` | seletor `publico`/`restrito`, pill de status do Atlas, botão "Nova conversa", "Reconectar" |
| `Welcome` | `frontend/src/components/Welcome.jsx` | 4 perguntas prontas para clicar (ninguém digita ao vivo em apresentação) e, no modo nativo, a linha de vantagens: 1 banco, 1 consulta, 0 pipelines de embedding, 0 serviços de rerank |
| `ChatMessage` | `frontend/src/components/ChatMessage.jsx` | renderiza Markdown da resposta, token a token |
| `ChatInput` | `frontend/src/components/ChatInput.jsx` | entrada de texto, bloqueada enquanto um turno está em andamento (`streaming`) |
| `EngineStrip` | `frontend/src/components/EngineStrip.jsx` | **a peça central da demo.** No caminho nativo o rótulo é `1 aggregation no Atlas`. Mostra o funil: N vetoriais + N léxicos → `$rankFusion` → N reranqueados (no caminho clássico, N fundidos (RRF)), modelo de embedding e de rerank, badge de nível de acesso e latência em ms. Não mostra dimensão nem nome de índice: no caminho nativo o Atlas gera os vetores, então não há dimensão do lado do app |
| `ArchitectureModal` | `frontend/src/components/ArchitectureModal.jsx` | o desenho "uma pergunta, uma aggregation": aplicação → cluster Atlas (`$vectorSearch` e `$search` → `$rankFusion` → `$rerank` → `$limit`, com chunks, vetores, conversas e checkpoints no mesmo cluster) → Claude via gateway, e a lista do que não precisou existir. Abre sob demanda pelo link **ver arquitetura** (no `Welcome`, sem números; no `EngineStrip`, só no caminho nativo, com os números daquela pergunta: quantos dos chunks finais vieram de cada ramo, ms da recuperação, filtro de ACL). Fecha com `Esc`, clique fora ou `×`. Se o nativo degradou, mostra o aviso e esmaece os estágios que não rodaram |
| `Sources` | `frontend/src/components/Sources.jsx` | os até 8 chunks que fundamentaram a resposta — cada um com badge `VETORIAL`/`LÉXICO` (de `matched_by`), badge `restrito` se aplicável, score `vetorial → rerank` (`—` no vetorial quando o chunk veio só da busca léxica) e preview do texto |
| `DocumentsPanel` | `frontend/src/components/DocumentsPanel.jsx` | biblioteca de documentos daquela aba (`workspace = base | uploads`): drag-and-drop de upload com barra de progresso por chunk, lista de documentos com checkbox para restringir a recuperação, tag `restrito`/`base`/tempo até expirar |
| `OfflineHero` | `frontend/src/components/OfflineHero.jsx` | tela de fallback quando `/api/config` ou o Atlas não respondem, com botão "Reconectar" |

Componentes legados (fora do shell atual, mantidos no repo mas não usados no fluxo principal): `Sidebar.jsx`, `KpiRow.jsx`.

## Por que o `EngineStrip` existe

"Busca híbrida" é fácil de afirmar e difícil de provar. Com o `EngineStrip` na tela, uma pergunta com número de processo/norma mostra a contribuição lexical subindo; uma pergunta conceitual/parafraseada mostra a vetorial subindo. O argumento comercial se demonstra sozinho, sem precisar narrar. Os números vêm direto do `stats` que `agent.py::retrieve_context` devolve (ver `queries.md`).

## Contrato de streaming (SSE)

`POST /api/chat` devolve Server-Sent Events com quatro tipos de evento, nesta ordem:

```
meta   → fontes recuperadas + funil de recuperação (stats)   — chega ANTES do primeiro token
token  → delta de texto (um ou mais por resposta)
done   → fim do turno
error  → mensagem de erro (substitui done)
```

Implementação client-side em `frontend/src/api.js::streamChat` — não usa `EventSource` (o endpoint é POST, `EventSource` só faz GET). Usa `fetch` + `getReader()`, acumulando buffer e quebrando em `\n\n`; trata `\r\n` (CRLF) e chunk incompleto que atravessa duas leituras. Timeout duro de **180s** para o chat inteiro (`deadline = setTimeout(abort, 180000)`), e trata EOF sem `done` como erro (`'A conexão terminou antes da resposta completa'`) em vez de simplesmente parar de atualizar a tela.

**A ordem `meta` antes de `token` é deliberada e não deve ser invertida por conveniência de implementação**: na demo, o cliente vê a recuperação (fontes + funil) aparecer primeiro, e só depois o texto sendo redigido — fica visualmente claro que o LLM escreveu em cima de uma busca real, não de memória.

Timeouts de outras operações (`frontend/src/api.js`): upload de documento 120s, config/status/histórico/jobs/delete 5–30s.

## Isolamento de workspace na tela

A separação entre as duas abas **não é garantida pelo frontend** — `WorkspaceView` manda `scope` (`"base"` ou `"uploads"`) em todo `POST /api/chat`, e é o backend quem resolve a lista de documentos correspondente (`documents.sources_for_scope`, ver `queries.md`). `sources` vazio no corpo da requisição significa "todos os documentos daquela aba", nunca "todo o corpus" — se fosse o frontend a montar essa lista, uma aba vazia acabaria respondendo pelo corpus base da outra aba, que é exatamente o vazamento que a separação existe para impedir. Uma aba sem conteúdo indexado recebe de volta `stats.mode = "empty_workspace"` e uma mensagem própria em vez de cair silenciosamente no corpus base.

## Controle de acesso na tela

O seletor `publico`/`restrito` do `TopBar` é **confiado do cliente** (não é autenticação — ver limitação em `architecture.md`). Existe na tela justamente para demonstrar, ao vivo, a **mesma pergunta devolvendo conjuntos de fontes diferentes** conforme o nível — o argumento visual é: alternar para `restrito` e ver conteúdo que antes não aparecia surgir na resposta e no painel de fontes.

## Nota sobre screenshots

`docs/screenshots/` tem 4 capturas (recapturadas em 2026-10-02 contra o pipeline nativo, com a identidade do tenant substituída no DOM antes de cada captura, inclusive em maiúsculas) (`01-home.png`, `02-answer.png`, `03-sources.png`, `04-upload.png`), a 1600×1000, tiradas contra um tenant real. **Antes de qualquer nova captura**: o app rodando pode mostrar organização, nome de banco e conteúdo processual reais no cabeçalho, na resposta e nas passagens citadas — substitua esses nomes por valores neutros no DOM (nós de texto e placeholders) imediatamente antes de cada captura, e mantenha, abaixo das imagens no README, a nota de que os nomes foram substituídos. Isso é ainda mais crítico aqui do que em outras PoVs porque o corpus é de um órgão público e pode conter dado sigiloso.

## Roteiro de demo (7 passos)

1. **Pergunta com termo exato** (número de norma/processo, sigla) — mostrar no `EngineStrip` que a busca lexical contribuiu.
2. **A mesma pergunta com outras palavras** — a busca vetorial contribui, e o `$rankFusion` entrega os dois ranqueamentos fundidos.
3. **Abrir o painel de fontes** — cada resposta cita os chunks que a fundamentaram, com badge de motor (`VETORIAL`/`LÉXICO`) e scores `vetorial → rerank`. Nenhuma resposta sem procedência visível.
4. **Alternar para `restrito`** — conteúdo que estava fora da resposta aparece. Explicar que o filtro está dentro dos dois estágios de busca, nunca aplicado depois da fusão.
5. **Recarregar a página e retomar pelo `thread_id`** — a conversa está persistida no próprio Atlas (`conversations`), não em memória do processo.
6. **Ingerir um documento novo** (outro formato — XLSX ou PPTX) pela CLI ou arrastando na aba `Novo conteúdo`, marcar só ele e repetir uma pergunta do documento original — o assistente diz que aquilo não está no contexto. Prova de que o filtro por `metadata.source` roda dentro das duas buscas, e é o momento em que o cliente entende que pode trazer o próprio documento (inclusive uma peça ou norma do próprio órgão) para a reunião.
7. **Trocar `CLIENT_ID` no `.env`** — outro database, outro documento, outra persona, **mesmo código rodando**. É o fecho que transforma "fizeram uma demo para um cliente" em "isto é uma plataforma".

Antes de apresentar: `setup_db_native.py` já rodado com os dois índices `READY` (o `autoEmbed` leva cerca de um minuto e meio para embedar o que foi inserido), `RAG_NATIVE=1` e `DB_NAME` apontando para a base nativa; uma pergunta de aquecimento fora da demo para pagar o cold start de embedding/geração; seletor de acesso começando em `publico` (para o passo 4 ter contraste).


## Roteiro curto para mostrar o MongoDB 9

1. Abrir com a tela inicial: a linha "1 banco · 1 consulta · 0 pipelines de embedding · 0 serviços de rerank" é a tese.
2. Fazer uma pergunta e clicar em **ver arquitetura** no strip: o desenho acende com os números daquela pergunta e fecha com a lista do que não precisou existir (ETL, banco vetorial, motor de busca, APIs de embedding e rerank).
3. Abrir **Ver query / chamada executada**: é um único `aggregate` com `$rankFusion` e `$rerank`, e os filtros de tenant e de acesso estão dentro de cada ramo.
4. Abrir o painel de fontes: os badges `VETORIAL`/`LÉXICO` vêm do `scoreDetails` do próprio `$rankFusion`.
5. Na aba `Novo conteúdo`, enviar um documento e perguntar sobre ele em seguida: o Atlas gera os vetores sozinho (`autoEmbed`), sem chamada de embedding no código da aplicação.
6. Alternar para `restrito` para mostrar a ACL, e fechar com o que ainda é Preview (`autoEmbed`, `rerank-3`).
