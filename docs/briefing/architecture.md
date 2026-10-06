# Arquitetura — RAG multi-tenant

> Objetivo deste documento: em 5 minutos, qualquer engenheiro (ou o gestor perguntando "como isso funciona?") entende o desenho, a stack e o porquê de cada decisão. Consultas concretas (queries, índices, pipelines) estão em `queries.md`. Telas e fluxos de UI estão em `ui-flows.md`.

## O que é

Assistente RAG (Retrieval-Augmented Generation) sobre **MongoDB Atlas Vector Search**, feito para responder perguntas em cima de documentos institucionais (deploy de referência: documento público de planejamento de TI — PDTIC). A stack é deliberadamente **agnóstica de documento e de tenant**: trocar de cliente é trocar variáveis de `.env`, não código.

Critério que define "arquitetura certa" aqui: adicionar um tenant novo = novos valores no `.env` + documento em `data/` + `client_config.json`. Zero mudança de código Python.

## Stack

| Camada | Tecnologia | Onde |
|---|---|---|
| Banco / busca | MongoDB Atlas (Vector Search + Atlas Search/BM25) | `db.py`, `setup_db.py` |
| Embeddings + rerank | Nativo no Atlas (`RAG_NATIVE=1`): Automated Embedding `voyage-4`, `$rankFusion`, `$rerank` `rerank-3`. Caminho clássico: VoyageAI `voyage-3` + `rerank-2` via SDK | `native_retrieval.py`, `agent.py`, `ingest.py` |
| Geração | Claude (`claude-sonnet-4-6`) via `langchain_anthropic.ChatAnthropic`, sempre atrás de um gateway explícito: `GROVE_BASE_URL` validado pelo `grove_client` do pacote `pov-shared`, ou `ANTHROPIC_BASE_URL` declarado; sem destino padrão implícito | `llm_gateway.py`, `backend/api.py` |
| API | FastAPI, streaming SSE | `backend/api.py` |
| Frontend | React + Vite + LeafyGreen (design system MongoDB) | `frontend/src/` |
| Deploy | container único: nginx serve o build do frontend e faz proxy de `/api` pro uvicorn | `Dockerfile`, `docker/start.sh`, `nginx.conf` |

## Fluxo de dados (pergunta → resposta)

```
Usuário digita/clica pergunta na UI
  → POST /api/chat (SSE)
  → backend/api.py resolve escopo (workspace/tenant/ACL)
  → agent.py::retrieve_context(query)
       Caminho nativo (RAG_NATIVE=1), um único aggregation no Atlas:
       1. $rankFusion com dois ramos: $vectorSearch (autoEmbed voyage-4: o Atlas
          embeda a pergunta) e $search (BM25), cada um filtrado por client_id +
          nivel_acesso + source
       2. $rerank (rerank-3) reordena o conjunto fundido; $limit 8
       3. scoreDetails devolve o ramo (e o score bruto) de cada chunk: alimenta os badges
          Falha do Atlas degrada para lexical-only (native_degraded)
       Caminho clássico (RAG_NATIVE=0): embed voyage-3 com cache LRU, $vectorSearch e
       $search em paralelo, RRF k=60 em Python, rerank-2 pelo SDK da Voyage
  → backend monta SystemMessage/HumanMessage/AIMessage (à mão, sem template)
  → Claude gera a resposta em streaming (SSE: meta → token* → done)
  → UI mostra EngineStrip + Sources (evidência) ANTES do texto, depois o
    texto token a token
  → conversa persistida em MongoDB (coleção `conversations`, best-effort)
```

O evento `meta` (fontes + stats do funil de recuperação) chega **antes** do primeiro `token`, de propósito: na demo, o cliente vê a recuperação acontecer antes da redação — fica claro que o LLM respondeu em cima de busca real, não de memória.

## Por que um banco só

![Stack RAG típica, com seis sistemas e dois pipelines de sincronia, ao lado desta PoV, em que embedding, busca léxica e vetorial, fusão e rerank rodam numa aggregation em um cluster Atlas](../architecture/one-database.svg)

Numa stack RAG típica, cada estágio da recuperação é um sistema à parte: o banco OLTP guarda conversas, usuários e ACL; um ETL/CDC copia os dados para um motor de busca (BM25) e para um banco vetorial; uma API de embedding é chamada a cada pergunta e a cada chunk; uma API de rerank tem chave, cota e fallback próprios; e a fusão dos rankings é código na aplicação. No caminho nativo desta PoV (`RAG_NATIVE=1`), tudo isso vira **uma aggregation** na coleção `documents` (`native_retrieval.py`), e os dados operacionais ficam no mesmo cluster:

| Peça | Stack típica | Nesta PoV |
|---|---|---|
| Dado operacional (conversas, checkpoints do LangGraph, ACL, TTL) | banco OLTP | mesmo cluster Atlas (`conversations`, `rag_<CLIENT_ID>`, `metadata.*`) |
| Vetores | banco vetorial + sincronia | `$vectorSearch` com `autoEmbed` na mesma coleção; o Atlas mantém os vetores |
| Busca léxica | motor de busca + ETL | `$search` (BM25) no `text_index` |
| Embedding | API externa chamada pela app | `voyage-4`, gerado pelo Atlas |
| Fusão | código na app | `$rankFusion` |
| Rerank | API externa | `$rerank` (`rerank-3`) |

Medido nesta PoV (caminho clássico contra nativo, mesmos 303 chunks): 4 idas por pergunta viram 1, a recuperação cai de 852 ms para 743 ms e nenhuma chave de IA externa é usada na recuperação (testado com `VOYAGE_API_KEY` vazia). Só a geração sai do banco (Claude via gateway). O ganho é de **operação** (menos sistemas, sem sincronia, ACL e filtro de tenant num lugar só), não de qualidade: com rerank, a diferença de recall é ruído. `autoEmbed` e `rerank-3` estão em Preview.

## Por que híbrido (vetorial + lexical) e não só vector search

Decisão central da PoV, e a pergunta que sempre aparece em reunião:

- **Vetorial sozinho** erra em sigla, número de norma/processo e nome próprio — comum em documento jurídico/administrativo.
- **Lexical sozinho** erra quando o usuário pergunta com outras palavras (paráfrase).
- **RRF** funde os dois ranqueamentos sem precisar normalizar scores de escalas diferentes (que nem são comparáveis entre si).
- **Rerank** é o estágio que mais melhora a resposta por token gasto — separa "8 chunks que casaram" de "os 8 chunks que respondem".

As duas buscas rodam de fato em paralelo (`ThreadPoolExecutor`, uma thread para a vetorial) e **cada uma tolera a falha da outra**: se o índice vetorial cair, a lexical sozinha responde (e vice-versa), com log indicando qual caiu. O rerank também degrada: se a chamada à VoyageAI falhar, mantém a ordem do RRF. Ver `agent.py::retrieve_context` e detalhamento em `queries.md`.

## Componentes principais

| Arquivo | Papel |
|---|---|
| `agent.py` | entrada da recuperação: caminho nativo (`native_retrieval.py`, uma aggregation) ou clássico (embedding → vector search + lexical search → RRF → rerank), filtro de prompt injection nos trechos recuperados. Chamado pelo nó `retrieve` de `rag_graph.py`. |
| `rag_graph.py` | o turno de chat como `StateGraph` do LangGraph: `retrieve` → (`refuse` \| `generate`), checkpoint em `MongoDBSaver` (banco `rag_<CLIENT_ID>`, TTL de 30 dias) ou `MemorySaver` sem `MONGO_URI`. |
| `backend/api.py` | app FastAPI: streaming SSE do chat, montagem manual das mensagens (não usa `ChatPromptTemplate` porque contexto/histórico podem conter `{}` literais que um template interpretaria como variável), outline cacheado, endpoints de config/status/histórico/documentos. |
| `backend/documents.py` | biblioteca de documentos: valida upload, enfileira ingestão em worker único, expõe jobs, separa os dois "workspaces" (corpus base vs. uploads de demo) via TTL, protege o corpus base contra remoção. |
| `ingest.py` | loader multi-formato (PDF/DOCX/TXT/CSV/MD/HTML/JSON/XLSX/PPTX), chunking (`RecursiveCharacterTextSplitter`, 800/150), embedding em lotes com pausa de 22s (tier gratuito VoyageAI: 3 req/min). |
| `db.py` | `MongoClient` singleton (pool de conexão reusado em todo o processo) + verificação de identidade do tenant no boot. |
| `config.py` | toda a configuração de tenant vem de variáveis de ambiente (`CLIENT_ID`, `CLIENT_NAME`, etc.), sem default silencioso para `CLIENT_ID`. |
| `setup_db_native.py` / `setup_db.py` | scripts administrativos idempotentes (nativo / clássico): coleções, índices TTL e os dois índices de busca (`vector_index`, `text_index`). Recusam banco que não termina em `_test` sem `ALLOW_DEMO_DB_WRITE=1`. |
| `scripts/reset_demo.py` | reset da demo num comando: índices, reingestão do corpus base (o Atlas regenera os embeddings via `autoEmbed`), remoção de uploads e fontes fora do corpus, limpeza de conversas e checkpoints, espera até uma consulta real devolver trechos. Mesma guarda de banco. |
| `observability.py` | logging estruturado, request-id, `/api/metrics`, `/metrics` (Prometheus), `/api/health`. |

## O turno como grafo (LangGraph), sem agente

`rag_graph.py` organiza o turno em três nós: `retrieve` (chama `agent.retrieve_context`, decide se há evidência), `refuse` (recusa sem chamar o LLM, só com `RAG_REFUSE_WEAK_EVIDENCE=1` ou sem trecho nenhum) e `generate` (stream do Claude). Não há ferramentas nem laço de raciocínio: é um pipeline fixo, por isso não existe `agent-behavior.md`. O endpoint `/api/chat` é síncrono e roda o grafo numa thread, drenando uma fila para o SSE.

Checkpoints: `MongoDBSaver` grava em `rag_<CLIENT_ID>` (`langgraph_checkpoints`, `langgraph_checkpoint_writes`) com TTL (`CHECKPOINT_TTL_DAYS`, padrão igual a `CONVERSATION_RETENTION_DAYS`, 30). Um turno sem `thread_id` recebe uma thread própria (`anon-<uuid>`); antes todos iam para a mesma thread nula, empilhando estado de pessoas diferentes.

## Multi-tenancy

Um database Atlas por tenant: `rag_<CLIENT_ID>`. `CLIENT_ID` nomeia o banco e é obrigatório — o boot falha sem ele (a menos que `ALLOW_DEFAULT_TENANT=true`, modo dev explícito). `db.py::verify_tenant_identity` grava/confere uma identidade carimbada em `_meta` para pegar um `MONGO_URI` reaproveitado de outro tenant (erro operacional plausível: `.env` copiado ou esquecido).

Dentro de um tenant, dois "espaços de trabalho" isolados na mesma base/coleção:

- **`base`** — corpus de referência, ingerido por CLI, nunca expira, não pode ser removido pela tela (403).
- **`uploads`** — conteúdo enviado durante a demo pela UI, expira sozinho via TTL (`UPLOAD_TTL_HOURS`, padrão 24h).

A fronteira entre os dois é a **presença/ausência do campo `metadata.expires_at`** — nenhum banco, índice ou campo novo foi criado para isso. Ver `queries.md` para os detalhes de índice/TTL e `ui-flows.md` para como isso aparece nas duas abas.

## Controle de acesso (`nivel_acesso`) — conceito só da PoC

`"publico"` ou `"restrito"`, escolhido pelo **cliente** na UI (não por autenticação real). Default `publico` nos dois lados (front e back) — o caminho mais permissivo nunca é o default implícito. Filtrado nos dois estágios de busca (vetorial e lexical), como filtro nativo dos índices — nunca depois da fusão, para que conteúdo restrito não influencie sequer o ranqueamento do que o usuário pode ver.

**Isso não é controle de acesso de produção.** Em um deployment real para o tribunal, isso precisa vir de autenticação real (SSO/JWT) associada a perfil/lotação do usuário — nunca de um campo aceito do corpo da requisição. Ver `## Exposição da API` abaixo.

## Isolamento de dados multi-tenant (o que importa para dado jurídico)

Duas camadas de isolamento, propositalmente redundantes:

1. **Banco por tenant** — cada tenant tem seu próprio `rag_<CLIENT_ID>`. Hoje é a barreira real: não há como uma query de um tenant alcançar o banco de outro.
2. **`metadata.client_id` como filtro obrigatório** em toda busca (`_vector_pipeline`/`_lexical_pipeline` em `agent.py`) e como campo de filtro nos dois índices Atlas Search. Hoje isso é redundante (um `client_id` por banco = sempre bate), mas vira uma segunda barreira real se a topologia migrar no futuro para banco compartilhado entre tenants. Comentário explícito no código: "defense in depth".

Essa camada 2 é o tipo de detalhe que vale mostrar em auditoria de segurança: o isolamento não depende de uma única garantia (banco separado), há defesa em profundidade mesmo que hoje pareça redundante.

## Decisões de custo/resiliência

- **Histórico do cliente tem teto** — por número de mensagens (16) e por caracteres (48000), senão o custo por turno cresce ao longo da conversa.
- **Concorrência de geração limitada por semáforo** (`RAG_MAX_CONCURRENCY`, padrão 4) — satura, recusa (HTTP 429) em vez de enfileirar.
- **Teto de tokens de saída** (`ANTHROPIC_MAX_TOKENS`, padrão 1500).
- **Prompt caching do Claude**: o bloco de instruções estáticas (~120 tokens) sozinho não passa do mínimo de ~1024 tokens que a Anthropic exige para efetivar cache — por isso o sumário/outline do documento (estável entre turnos, cacheado 1h em memória) é anexado ao mesmo bloco, empurrando-o acima do mínimo e tornando o `cache_control: ephemeral` real, não um no-op silencioso.
- **TTL em conversas** (`CONVERSATION_RETENTION_DAYS`, padrão 30 dias), **nos checkpoints do LangGraph** (mesmo prazo) e **em uploads de demo** (24h) — dado de demo que fica para sempre vira custo para sempre.
- **Resiliência ligada por padrão** (a variável só ajusta ou desliga): retry do Claude antes do primeiro token (`LLM_STREAM_MAX_ATTEMPTS=3`, só 429/5xx/timeout/conexão), teto de geração (`STREAM_DEADLINE_S=120`), retry da Voyage no caminho clássico (`VOYAGE_MAX_ATTEMPTS=3`) e filtro heurístico de injection nos trechos (`RAG_INJECTION_FILTER=1`, fail-open sem o pacote `guardrails`). Atlas sem responder em nenhum caminho de busca vira erro legível, não recusa por falta de evidência.

## Exposição da API — limitações documentadas, não escondidas

- `ALLOWED_ORIGINS` restringe quem chama a API (default: só o dev server local).
- `/api/chat` valida pergunta não-vazia e < 4000 caracteres antes de gastar uma chamada de LLM.
- `/api/health` responde 503 (não 200 com campo "degradado") quando o Atlas não responde.
- **Sem autenticação em nenhum endpoint.** `access_level` é confiado do corpo da requisição. `/api/history/{thread_id}` devolve qualquer conversa a quem souber/adivinhar o `thread_id` (UUIDv4 gerado no cliente). Upload e remoção de documentos são abertos (validam formato/tamanho, não autenticam quem envia).
- A remoção de documentos atinge só uploads de demo — o corpus base devolve 403 e não tem botão na UI. Um upload com o mesmo nome de um documento base é recusado (422): com "reindexar" marcado, ele chegava a `ingest(reset=True)` e o `delete_many` por `metadata.source` apagava o corpus (reproduzido em banco `_test` em 2026-10-06: 303 → 0 chunks permanentes).

**Isto é uma limitação de PoC, não uma feature.** Qualquer deployment que não seja demo em ambiente controlado precisa de autenticação real (SSO/JWT) antes de subir — e, dado que o corpus pode conter dado processual, isso vale para qualquer ambiente que não seja isolado/descartável.

## Como rodar (referência rápida)

```bash
./run.sh                 # backend 127.0.0.1:8180 + frontend 127.0.0.1:5180
ALLOW_DEMO_DB_WRITE=1 python scripts/reset_demo.py <arquivos do corpus>   # índices + corpus + embeddings
python ingest.py <arquivo>   # ingestão avulsa via CLI (mesma guarda de banco)
python -m unittest discover -s tests -v   # testes de lógica pura, sem Atlas/Voyage/Anthropic ao vivo
```

Comandos completos: ver o README.


## Resiliência verificada (2026-10-02)

Bateria executada contra o cluster real e um backend vivo, com os achados:

- **Vazamento de slot de concorrência** (corrigido): cada cliente que desconectava no meio do stream deixava um slot de `RAG_MAX_CONCURRENCY` preso; quatro quedas faziam a API responder 429 até reiniciar. O slot agora acompanha a thread que gera (`SlotLease` em `backend/api.py`), a geração para quando o cliente some e `tests/test_slot_lease.py` cobre a liberação única.
- **Fuso do TTL** (corrigido): `expires_at` saía sem fuso e a UI mostrava 26 h para um TTL de 24 h em UTC-3. `backend/documents.py::iso_utc` serializa em UTC.
- **Passou sem mudança:** entradas hostis (operadores Mongo, `$where`, prompt injection, unicode, marcadores `{}`), validação de payload, isolamento por `client_id` e por `nivel_acesso` nos dois ramos, reranker ou modelo de embedding inválido (degrada para lexical-only), banco sem índice, gateway do LLM fora do ar (erro legível em segundos), consulta sem `VOYAGE_API_KEY`.
- **Limitação conhecida:** o `rank: "NA"` do `scoreDetails` (ramo em que o chunk não apareceu) precisa ser ignorado; só `rank` inteiro conta como acerto daquele ramo.
