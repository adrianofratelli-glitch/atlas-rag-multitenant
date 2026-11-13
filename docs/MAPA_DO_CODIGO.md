# Mapa do código — atlas-rag-multitenant

> Também conhecida como: RAG multi-tenant (aba Graph-RAG). Portas: frontend 5180 / backend 8180. Em uma frase: perguntas em linguagem natural sobre documentos longos, respondidas com citações, onde busca vetorial, lexical, fusão e rerank rodam num único aggregation do Atlas (e, na aba Graph-RAG, o mesmo aggregation também percorre um grafo de conhecimento guardado no MongoDB).

Os caminhos abaixo são relativos à raiz do repositório. Complementos: [architecture](briefing/architecture.md), [queries](briefing/queries.md), [ui-flows](briefing/ui-flows.md).

## 1. Árvore anotada

```
backend/                  todo o código Python do servidor (FastAPI), rodado como `uvicorn backend.api:app`
  api.py                  rotas HTTP, streaming SSE do chat, outline do documento, salvar conversa
  rag_graph.py            fluxo de um turno de chat em LangGraph: retrieve -> (refuse | generate); checkpoints no Mongo
  agent.py                escolhe o motor de recuperação (clássico, nativo, grafo) e monta o contexto do prompt
  native_retrieval.py     pipeline nativo: $vectorSearch + $search -> $rankFusion -> $rerank (um aggregation só)
  graph_retrieval.py      pipeline do Graph-RAG em kg_entities: entrada, $lookup, $graphLookup, evidência, rerank
  graph_extract.py        extração de entidades/relações pelo LLM e resolução de duplicatas (usado no build do grafo)
  build_graph.py          constrói/atualiza o grafo (kg_entities, kg_communities); `python -m backend.build_graph`
  documents.py            biblioteca de documentos: listar, upload com job em background, apagar, esperar indexar
  ingest.py               ingestão (carregar arquivo, quebrar em chunks, inserir); `python -m backend.ingest`
  db.py                   cliente MongoDB único + trava de identidade do tenant (`tenant_identity`)
  config.py               variáveis de ambiente e `client_config.json` (nome do tenant, perguntas iniciais, flags)
  llm_gateway.py          decide o destino do LLM (Grove via pov-shared ou ANTHROPIC_BASE_URL explícito)
  resilience.py           retry/backoff e degradação
  observability.py        métricas e logs; telemetry.py: spans de tracing opcional (TRACE_SINK)
frontend/                 React + Vite (porta 5180, proxy /api -> 8180)
  src/App.jsx             casca da tela: abas, barra superior, status
  src/api.js              TODAS as chamadas HTTP do front (axios + fetch para o stream SSE)
  src/components/         WorkspaceView (chat), CompareView (lado a lado), GraphTab, DocumentsPanel (upload),
                          Graph*/Architecture* (painéis e modais explicativos), TopBar, Sources, QueryDetails
  src/graphLayout.js      layout do desenho do subgrafo (só visual)
  tests/                  testes de node (streaming SSE e layout do grafo)
scripts/                  setup e utilitários de linha de comando
  setup_db_native.py      cria coleções, TTLs e os índices vector_index/text_index (caminho nativo, RAG_NATIVE=1)
  setup_db.py             idem para o caminho clássico (embedding no cliente via Voyage)
  reset_demo.py           volta a demo ao estado limpo (re-ingere corpus, apaga uploads, limpa conversas)
  graph_scale_test.py, graph_entry_scale_test.py   testes de escala do grafo em banco `*_test` descartável
tests/                    unittest em Python (lógica pura, sem rede)
eval/                     avaliação quantitativa (golden set, ragas, auditoria do grafo, rótulo humano); `eval/run_all.sh`
bench/                    benchmark de recuperação/geração
data/                     documentos do tenant (ignorado pelo git)
docs/briefing/            briefings públicos (arquitetura, queries, fluxos de UI)
docker/, Dockerfile, nginx.conf   empacotamento
run.sh                    sobe/para backend e frontend em background (`./run.sh`, `stop`, `status`)
client_config.example.json, .env.example   modelos de configuração do tenant
```

## 2. "Apertei o botão X, o que roda?"

O front inteiro conversa com o backend por 8 funções em `frontend/src/api.js`. O chat sempre usa `POST /api/chat`; o que muda é o `scope` (aba) e o `engine`.

| Tela / ação | Componente (arquivo:linha) | Chamada HTTP | Handler (arquivo:linha) | Função de negócio | Onde toca o MongoDB |
|---|---|---|---|---|---|
| Abrir a página (config e nome do tenant) | `frontend/src/App.jsx:72` (`getConfig`) | `GET /api/config` | `backend/api.py:539` `api_config` | `get_graph_status` (`backend/api.py:330`) | `_meta` (`graph_build`) via `find_one` (`api.py:333`), só se `RAG_NATIVE=1` |
| Pílula "Atlas Online/Offline", "Reconectar" | `frontend/src/App.jsx:63`; `TopBar.jsx:24` | `GET /api/status` (`force=true` no Reconectar) | `backend/api.py:588` `api_status` | `get_status` (`api.py:282`) | `_ping_atlas` (`api.py:269`): comando `ping` e contagem de chunks |
| Alternar perfil `publico` / `restrito` | `frontend/src/components/TopBar.jsx:15` -> `setAccessLevel` (`frontend/src/App.jsx:59`) | `POST /api/session` (token restrito; `publico` descarta o token) | `backend/api.py:568` `api_session` -> `backend/auth.py::issue_token` | cada chamada seguinte manda `Authorization`; `_granted_level` (`backend/api.py:479`) verifica e `_levels_for` (`api.py:475`) converte em filtro | filtro `metadata.nivel_acesso` nos pipelines, no grafo, na listagem e no histórico |
| "Nova conversa" | `TopBar.jsx:21` -> `App.jsx:97` `newChat` | nenhuma (remonta o componente, novo `threadId`) | — | — | — |
| Abas "Corpus de referência", "Graph-RAG", "Novo conteúdo" | `frontend/src/App.jsx:135` (`TABS` em `App.jsx:25`) | nenhuma | — | — | — |
| Lista de documentos (painel da aba) | `frontend/src/components/DocumentsPanel.jsx:52,60` (`getDocuments`) | `GET /api/documents` | `backend/api.py:636` `api_documents` | `backend/documents.py::list_documents` (124), filtrada pelo nível do token | `documents`: `aggregate` `$group` + `$facet` (`documents.py:149`) |
| Enviar arquivo (aba "Novo conteúdo") | `DocumentsPanel.jsx:99` `send` (`uploadDocument`); botão `DocumentsPanel.jsx:155` | `POST /api/documents` (multipart) | `backend/api.py:657` `api_upload_document` (restrito exige token restrito) | `documents.py::start_ingestion` (395) -> `_run_job` (439) -> `ingest.py::ingest` (166) -> `wait_until_searchable` (331) -> `_build_upload_graph` (256) | `documents`: `insert_many` (`ingest.py:45` nativo, `ingest.py:298` clássico), `delete_many` se "reindexar" (`ingest.py:222`); espera via `$searchMeta`/`$vectorSearch` (`documents.py:275,296`); grafo do upload: `build_graph.py::build_upload_graph` (500) em `kg_entities` |
| Acompanhar o envio (barra de progresso) | `DocumentsPanel.jsx:71` (`getJob`, polling) | `GET /api/documents/jobs/{job_id}` | `backend/api.py:685` `api_document_job` | `documents.py::get_job` (369) | nenhum (job fica em memória) |
| Apagar documento enviado | não há botão: `deleteDocument` existe em `api.js:50` mas nenhum componente o chama | `DELETE /api/documents/{source}` | `backend/api.py:693` `api_delete_document` | `documents.py::delete_document` (281), `delete_upload_graph` (271) | `documents`: `delete_many` (`documents.py:252`); `kg_entities`: `delete_many` (`build_graph.py:549`); proteção do corpus base em `is_protected` (`documents.py:217`) |
| Perguntar na aba "Corpus de referência" (clique numa pergunta sugerida ou ENTER no input) | `frontend/src/components/WorkspaceView.jsx:46` `send` (sugestões: `WorkspaceView.jsx:143`; input: `ChatInput.jsx:32`) | `POST /api/chat` (SSE) com `scope:"base"`, `engine:"hybrid"` (`frontend/src/api.js:59`) | `backend/api.py:715` `api_chat` | `rag_graph.py::run_turn` (192) -> `n_retrieve` (52) -> `agent.py::retrieve_context` (151) -> `_retrieve_native` (218) -> `native_retrieval.py::run_native` (123); depois `n_generate` (`rag_graph.py:89`) chama o LLM | `documents`: `aggregate` com `$rankFusion` (`native_retrieval.py:129`); outline: `api.py::_get_document_outline` (`api.py:415`); salvar conversa: `_save_conversation` (`api.py:498`, coleção `conversations`); checkpoints do LangGraph (`rag_graph.py:158`) |
| Perguntar na aba "Novo conteúdo" (alternar Híbrido / Graph-RAG: `WorkspaceView.jsx:110`) | `WorkspaceView.jsx:46` | `POST /api/chat` com `scope:"uploads"` e `engine` escolhido | `backend/api.py:715` | mesmo caminho; `documents.sources_for_scope` (184) limita aos uploads | igual à linha anterior; com `engine:"graph"` usa `kg_entities` com `scope:"uploads"` |
| Perguntar na aba "Graph-RAG", modo "Híbrido + grafo" | `frontend/src/components/GraphTab.jsx:35` troca o modo; `WorkspaceView` com `engine="graph"` (`GraphTab.jsx:39`) | `POST /api/chat` com `scope:"base"`, `engine:"graph"` | `backend/api.py:715` (valida `RAG_NATIVE=1` logo no início) | `agent.py::_retrieve_graph` (271) -> `graph_retrieval.py::run_graph` (372, calcula `documents.denied_sources`), que usa `build_graph_pipeline` (62); a evidência entra no contexto via `_retrieve_native` (`agent.py:288`) | `kg_entities`: `aggregate` (`graph_retrieval.py:380`): `$rankFusion` (entrada) + `$lookup` + `$graphLookup` + `$rerank`; chunks de evidência vêm de `documents` pelo `$lookup` |
| Aba Graph-RAG, modo "Lado a lado: base × base + grafo" | `frontend/src/components/CompareView.jsx:34` `send` (dois `streamChat`, linha 42) | dois `POST /api/chat` em paralelo (`engine:"hybrid"` e `engine:"graph"`, `scope:"base"`) | `backend/api.py:715` | os dois caminhos acima, simultâneos | os dois aggregations acima |
| "ver arquitetura" / "ver travessia" / "ampliar" | `EngineStrip.jsx:66,102`; `Welcome.jsx:39`; `GraphWelcome.jsx:44`; `GraphPanel.jsx:67` | nenhuma (modais com dados do último `meta` do stream) | — | `ArchitectureModal.jsx`, `GraphModal.jsx` | — |
| Retomar conversa antiga | não usado na UI: `getHistory` (`api.js:25`) não tem chamador | `GET /api/history/{thread_id}` | `backend/api.py:628` `api_history` (nível do token) | `_load_conversation` (`api.py:525`) | `conversations`: `find_one` por `_id` + `client_id`, nível conferido (`api.py:527`) |
| Métricas e saúde (operação, sem botão) | — | `GET /api/metrics`, `/metrics`, `/api/health`, `/health/live` | `backend/api.py:168,174,179,186` | `observability.metrics` | — |

O que o stream devolve: eventos `meta` (fontes, estatísticas, tempo, perguntas de acompanhamento), `token` (texto) e `done`/`error`; o front os trata em `frontend/src/api.js:59` (`streamChat`).

## 3. Onde estão as queries MongoDB

Detalhes e exemplos mascarados em [docs/briefing/queries.md](briefing/queries.md).

| O que faz | arquivo::função (linha) | Operador / pipeline principal | Coleção |
|---|---|---|---|
| Busca híbrida nativa (padrão com `RAG_NATIVE=1`) | `backend/native_retrieval.py::build_native_pipeline` (13), executado em `run_native` (129) | `$rankFusion` com `$vectorSearch` (autoEmbed, `voyage-4`) e `$search` + `$rerank` + `$limit` | `documents` |
| Fallback só lexical se a nativa falha | `native_retrieval.py::_lexical_fallback` (85), executado em `run_native` (135) | `$search` (compound) + `$limit` | `documents` |
| Busca vetorial clássica (`RAG_NATIVE=0`) | `backend/agent.py::_vector_pipeline` (88), executada em `_run_vector` (389) | `$vectorSearch` sobre `embedding` | `documents` |
| Busca lexical clássica | `agent.py::_lexical_pipeline` (112), executada em `_run_lexical` (401) | `$search` compound + `$limit` | `documents` |
| Graph-RAG: entrada, travessia e evidência num só aggregation | `backend/graph_retrieval.py::build_graph_pipeline` (62), executado em `run_graph` (372) | `$rankFusion` (`kg_vector_index` + `kg_text_index`), `$lookup` (hop 1), `$graphLookup` (hop 2, com corte de hubs), `$group`, `$lookup` para chunks com `$rerank`, `$unionWith` em `documents` (trechos diretos, `graph_retrieval.py:174`) e em `kg_communities` (sumários) | `kg_entities` (+ `documents`, `kg_communities`) |
| Outline do documento (mapa de citação no prompt) | `backend/api.py::_get_document_outline` (411) | `aggregate` com `$match`, `$group` por fonte/página | `documents` |
| Listar documentos | `backend/documents.py::list_documents` (124) | `$match`, `$group`, `$facet` | `documents` |
| Fontes de cada aba (corpus × uploads) | `documents.py::sources_for_scope` (184) | `distinct("metadata.source")` | `documents` |
| Documento protegido? | `documents.py::is_protected` (217-219) | `count_documents` | `documents` |
| Esperar o upload ficar pesquisável | `documents.py::_lexical_count` (306), `_vector_hit` (318), `wait_until_searchable` (331) | `$searchMeta` (count), `$vectorSearch`, `find_one` | `documents` |
| Apagar documento | `documents.py::delete_document` (281) | `delete_many` por `metadata.source` | `documents` |
| Inserir chunks | `backend/ingest.py::insert_chunks_auto` (45), `ingest` (298) | `insert_many` | `documents` |
| Reindexar um documento | `ingest.py::ingest` (206, 216, 222) | `count_documents` + `delete_many` | `documents` |
| Salvar / ler conversa | `backend/api.py::_save_conversation` (498), `_load_conversation` (525) | `update_one` com upsert; `find_one` | `conversations` |
| Status do grafo | `backend/api.py::get_graph_status` (330) | `find_one` `_id:"graph_build"` | `_meta` |
| Trava de identidade do tenant (no boot) | `backend/db.py::verify_tenant_identity` (51-57) | `find_one` / `insert_one` `_id:"tenant_identity"` | `_meta` |
| Checkpoints do turno de chat | `backend/rag_graph.py::_build_graph` (158), `MongoDBSaver` | escrita do LangGraph | `langgraph_checkpoints`, `langgraph_checkpoint_writes` (banco `rag_<CLIENT_ID>`, ver `checkpoint_db_name`) |
| Construir o grafo base | `backend/build_graph.py::load_chunks` (106), `sync_collection` (288), `write_graph` (300) | `find` nos chunks, `bulk_write` por diferença de hash, `replace_one` no `_meta` | `documents` (leitura), `kg_entities`, `kg_communities`, `_meta` |
| Candidatos a duplicata no grafo | `build_graph.py::neighbours` (342) | `$vectorSearch` em `kg_vector_index` | `kg_entities` |
| Grafo de um upload | `build_graph.py::build_upload_graph` (500), `wait_upload_graph_searchable` (533), `delete_upload_graph` (552) | `find`, `bulk_write`, `$vectorSearch`, `delete_many` | `documents`, `kg_entities` |
| Índices do grafo | `build_graph.py::ensure_indexes` (221-226) e `_ensure_search_index` (212, 215) | `create_index` (`client_scope_type`, `links`, TTL `uploads_ttl`, `upload_source`), `create_search_index`: `kg_vector_index`, `kg_text_index`, `kg_community_vector_index` | `kg_entities`, `kg_communities` |
| Índices da base (nativo) | `scripts/setup_db_native.py` (29, 32, 51, 54) | TTL de `conversations.updated_at`, TTL `uploads_ttl`, `vector_index` (vectorSearch, autoEmbed), `text_index` (search) | `conversations`, `documents` |
| Índices da base (clássico) | `scripts/setup_db.py` (29, 38, 62-65, 78-81) | idem, com embedding do cliente | `conversations`, `documents` |
| Reset da demo | `scripts/reset_demo.py::main` (127, 129, 130, 136, 138, 139) | `delete_many` (uploads, fora do corpus, conversas, checkpoints) | `documents`, `kg_entities`, `conversations`, checkpoints |
| Testes de escala do grafo | `scripts/graph_scale_test.py` (101, 138, 142), `scripts/graph_entry_scale_test.py` (73, 99, 103, 105) | `$graphLookup`/pipeline real sobre grafo sintético; `insert_many`, `create_index`, `drop_database` | banco `*_test` descartável |

## 4. Compartilhado × só desta PoV

**Vem de `_shared` (pacote `pov-shared`, instalado com `pip install -e "../_shared[llm,guardrails]"`, ver README):**
- `grove_client` -> destino e credencial do LLM: `backend/llm_gateway.py::gateway_settings` (`from grove_client import client_settings`, linha 21). Só é usado se `GROVE_BASE_URL` estiver definido; senão cai em `ANTHROPIC_BASE_URL` explícito.
- `tracing` -> spans opcionais: `backend/telemetry.py` (`from tracing import init_tracing`, linha 24), ligado só com `TRACE_SINK` diferente de `off` (e força `TRACE_MASK_PII=1`).
- Nenhum outro módulo de `_shared` é importado em `backend/`. (Langfuse: não encontrado no código deste repo; o tracing é OpenTelemetry via `telemetry.py`.)

**Variáveis de ambiente:**
- Comuns (vêm do `.env.shared` via `sync_env`): `MONGO_URI`, `VOYAGE_API_KEY`, `GROVE_BASE_URL` (e a chave do gateway), `ANTHROPIC_*` quando sem Grove.
- Locais (só desta PoV): `CLIENT_ID` (obrigatório, define o tenant), `CLIENT_NAME`, `DOCUMENT_TITLE`, `DOCUMENT_DESCRIPTION`, `DB_NAME` (padrão `rag_<CLIENT_ID>`), `RAG_NATIVE` (liga o motor nativo e o Graph-RAG), `NATIVE_EMBED_MODEL`, `NATIVE_RERANK_MODEL`, `NATIVE_RERANK_CANDIDATES`, `GRAPH_*` (sementes, saltos, corte de hubs, extração, `GRAPH_ON_UPLOAD`), `UPLOAD_*`/`MAX_UPLOAD_MB`, `RAG_MAX_CONCURRENCY`, `RAG_REFUSE_WEAK_EVIDENCE`, `RAG_INJECTION_FILTER`, `CHECKPOINT_TTL_DAYS`, `CONVERSATION_RETENTION_DAYS`, `ALLOW_DEMO_DB_WRITE`, `TRACE_SINK`, `LOG_JSON`. A lista completa de `os.getenv` está em `backend/config.py`, `agent.py`, `documents.py`, `build_graph.py`, `graph_retrieval.py`.

**Coleções/bancos:** banco próprio `rag_<CLIENT_ID>` por tenant (um por PoV/tenant, sem compartilhar com outras PoVs); coleções `documents`, `conversations`, `_meta`, `kg_entities`, `kg_communities`. Checkpoints do LangGraph ficam em banco separado (`rag_<CLIENT_ID>` conforme `checkpoint_db_name`, `backend/rag_graph.py:134`). Scripts que escrevem recusam banco que não termina em `_test` sem `ALLOW_DEMO_DB_WRITE=1` (`backend/config.py:41`).

**Chamadas externas:**
- LLM (Claude via gateway): `backend/api.py::_get_llm` (66) monta o `ChatAnthropic` com `gateway_settings()`; geração em `backend/rag_graph.py::n_generate` (89). Extração do grafo: `build_graph.py::_llm` (73) e `_complete` (82).
- Embeddings: no caminho nativo, o Atlas gera (`autoEmbed`, `voyage-4`) dentro do aggregation, sem chave no momento da consulta. No clássico: `backend/agent.py::_get_voyage` (54), `_embed_query` (70) e `backend/ingest.py` (230).
- Rerank: `$rerank` dentro do aggregation (nativo); sem SDK.

## 5. "Quero mexer em..." (atalhos)

- Texto de tela, rótulos de abas -> `frontend/src/App.jsx:25` (`TABS`) e os componentes em `frontend/src/components/`.
- Rótulos do modo da aba Graph-RAG -> `frontend/src/components/GraphTab.jsx:5` (`MODES`).
- Perguntas sugeridas e nome do tenant -> `client_config.json` (modelo em `client_config.example.json`), lido em `backend/config.py`; perguntas do grafo: `GRAPH_QUESTIONS` em `backend/config.py:70`.
- Prompt do sistema -> `SYSTEM_PROMPT_STATIC` (`backend/api.py:297`) e `GRAPH_PROMPT_EXTRA` (`backend/api.py:318`); complemento por tenant: `SYSTEM_PROMPT_EXTRA`.
- Adicionar uma rota -> `backend/api.py` (padrão `@app.get/post`) e a função correspondente em `frontend/src/api.js`.
- Mudar a busca híbrida nativa (candidatos, filtros, rerank) -> `backend/native_retrieval.py::build_native_pipeline`.
- Mudar a travessia do grafo (sementes, saltos, corte de hubs) -> `backend/graph_retrieval.py::build_graph_pipeline`; constantes por env `GRAPH_*`.
- Mudar a extração/criação do grafo -> `backend/graph_extract.py` e `backend/build_graph.py`.
- Mudar quando o chat recusa por evidência fraca -> `backend/agent.py::insufficient_evidence` (139) e `RAG_MIN_RERANK_SCORE`.
- Mudar limites de upload, TTL do upload -> `backend/documents.py` (`UPLOAD_TTL_HOURS`, `MAX_UPLOAD_MB`) e `backend/config.py`.
- Mudar porta -> `run.sh` (`BACKEND_PORT`, `FRONTEND_PORT`) e `frontend/vite.config.js` (porta do front e alvo do proxy).
- Mudar o seed/corpus da demo -> arquivos em `data/` ou `DEMO_CORPUS`; reset em `scripts/reset_demo.py`.
- Mudar índices de busca -> `scripts/setup_db_native.py` (base) e `backend/build_graph.py::ensure_indexes` (grafo).

## 6. Testes

- Tudo em Python (sem rede, sem Atlas): `python -m unittest discover -s tests -v` (comando do README).
- `tests/test_agent_pipelines.py` — construtores de pipeline do caminho clássico.
- `tests/test_native_retrieval.py` — pipeline nativo, fusão e degradação.
- `tests/test_graph_rag.py` — extração, resolução de entidades, pipeline de travessia.
- `tests/test_graph_audit.py` — auditoria do grafo: lote falho não vira veredito vazio.
- `tests/test_chat_graph.py` — `/api/chat` sobre o StateGraph e streaming em thread + fila.
- `tests/test_api_helpers.py` — funções auxiliares de `backend/api.py`.
- `tests/test_documents.py` — biblioteca de documentos e uploads.
- `tests/test_tenant_isolation.py`, `test_auxiliary_context_isolation.py`, `test_tenant_identity_race.py`, `test_config_tenant.py` — isolamento e identidade do tenant.
- `tests/test_hardening_adversarial.py` — suíte adversarial (entrada hostil, injeção indireta, falhas de gateway/Voyage/Atlas).
- `tests/test_resilience_governance.py` — retry, degradação do rerank, recusa sem evidência.
- `tests/test_slot_lease.py`, `test_client_concurrency.py` — concorrência do chat e do cliente.
- `tests/test_human_review.py` — ferramenta de rótulo humano (kappa).
- Front: `frontend/tests/streaming.test.mjs` (parser SSE de `api.js`) e `graphLayout.test.mjs`; rodam com `node --test frontend/tests`. O `package.json` não define script `test` (não encontrado).
- Avaliação quantitativa: `./eval/run_all.sh` (ver README, seção "Quality and governance").
