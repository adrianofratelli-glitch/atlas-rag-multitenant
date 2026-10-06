# Queries, pipelines e índices MongoDB

> Toda query/aggregation/índice real do repositório, extraída do código (não inventada). Para cada uma: onde está, o que faz, exemplo mascarado, e por quê existe. Se você chegou aqui procurando "onde está a query X" para responder ao gestor, use os títulos abaixo como índice.

**Cuidado com dado sigiloso**: o corpus real de um tenant pode conter dado processual. Os exemplos abaixo usam nomes de arquivo e nomes de tenant fictícios (`documento-x.pdf`, `tenant-x`). Nunca cole aqui `_id`, `thread_id`, texto de chunk ou nome de banco de um tenant real.

Total nesta versão: **9 aggregation pipelines / operações de leitura ou escrita distintas** sobre `documents`/`conversations`, **4 índices** (2 de Atlas Search/Vector Search + 2 TTL), e as chamadas de escrita da ingestão.

---

## Pipeline nativo (RAG_NATIVE=1)

**Onde:** `native_retrieval.py::build_native_pipeline`. Um único aggregation substitui as duas buscas, o RRF em Python e o rerank pelo SDK.

```python
[
  {"$rankFusion": {"input": {"pipelines": {
      "vector":  [{"$vectorSearch": {"index": "vector_index", "path": "text", "query": "<pergunta>",
                   "model": "voyage-4", "numCandidates": 225, "limit": 15,
                   "filter": {"$and": [{"metadata.client_id": "tenant-x"},
                                       {"metadata.nivel_acesso": {"$in": ["publico"]}}]}}}],
      "lexical": [{"$search": {"index": "text_index", "compound": {
                   "must": [{"text": {"query": "<pergunta>", "path": "text"}}],
                   "filter": [{"in": {"path": "metadata.client_id", "value": ["tenant-x"]}}]}}},
                  {"$limit": 15}]}},
    "scoreDetails": True}},
  {"$set": {"fusion_score": {"$meta": "score"}, "score_details": {"$meta": "scoreDetails"}}},
  {"$match": {"text": {"$type": "string", "$ne": ""}}},      # $rerank falha se o campo não existir
  {"$rerank": {"model": "rerank-3", "query": {"text": "<pergunta>"}, "path": "text", "numDocsToRerank": 30}},
  {"$set": {"rerank_score": {"$meta": "score"}}},
  {"$limit": 8},
]
```

**Índice `vector_index` do caminho nativo** (`setup_db_native.py`): `{"type": "autoEmbed", "modality": "text", "path": "text", "model": "voyage-4"}` mais os três `filter` (`nivel_acesso`, `source`, `client_id`). Não há campo `embedding` nos documentos.

**Observação de `scoreDetails`:** o ramo em que o chunk não apareceu vem com `rank: "NA"` e `value: 0`; só `rank` inteiro conta como acerto daquele ramo.

## Índices

### 1. `vector_index` — Atlas Vector Search

**Onde:** `setup_db.py:47-63`

```python
vector_def = {
    "fields": [
        {"type": "vector", "path": "embedding", "numDimensions": 1024, "similarity": "cosine"},
        {"type": "filter", "path": "metadata.nivel_acesso"},
        {"type": "filter", "path": "metadata.source"},
        {"type": "filter", "path": "metadata.client_id"},
    ]
}
docs.create_search_index({"name": "vector_index", "type": "vectorSearch", "definition": vector_def})
# ou, se já existe:
docs.update_search_index("vector_index", vector_def)
```

**O que faz:** índice de busca vetorial sobre `documents.embedding` (1024 dimensões, porque o modelo de embedding é `voyage-3`), com similaridade coseno. Os três campos `filter` permitem restringir a busca vetorial por ACL (`nivel_acesso`), por documento (`source`) e por tenant (`client_id`) **dentro** do próprio `$vectorSearch` — não depois.

**Por que existe:** sem o campo `filter`, o controle de acesso e o isolamento multi-tenant teriam que ser aplicados depois da busca vetorial — o que deixaria conteúdo restrito/de outro tenant influenciar o ranqueamento (o candidato "ocuparia vaga" entre os `numCandidates`/`limit` antes de ser descartado). O script roda `update_search_index` mesmo quando o índice já existe: um índice antigo sem `metadata.client_id`, por exemplo, quebraria a garantia de isolamento em silêncio após um deploy.

### 2. `text_index` — Atlas Search (BM25 / lexical)

**Onde:** `setup_db.py:66-79`

```python
text_def = {"mappings": {"dynamic": False, "fields": {
    "text": {"type": "string"},
    "metadata": {"type": "document", "fields": {
        "nivel_acesso": {"type": "token"},
        "source": {"type": "token"},
        "client_id": {"type": "token"},
    }},
}}}
docs.create_search_index({"name": "text_index", "type": "search", "definition": text_def})
```

**O que faz:** índice de texto completo (BM25) sobre `documents.text`, com `nivel_acesso`/`source`/`client_id` indexados como `token` para viabilizar `compound.filter` no `$search`.

**Por que existe:** é a metade lexical da busca híbrida — cobre os casos em que busca vetorial erra (sigla, número de processo/norma, nome próprio exato). `dynamic: False` é deliberado: indexar campo que ninguém consulta é custo de índice sem retorno.

### 3. `updated_at_ttl` — TTL de conversas

**Onde:** `setup_db.py:26-30`

```python
db["conversations"].create_index(
    "updated_at",
    name="updated_at_ttl",
    expireAfterSeconds=int(timedelta(days=retention_days).total_seconds()),  # padrão 30 dias
)
```

**Por que existe:** dado de demo que nunca expira vira custo (e volume) para sempre. `CONVERSATION_RETENTION_DAYS` controla a janela.

### 4. `uploads_ttl` — TTL de uploads de demo

**Onde:** `setup_db.py:35-39`

```python
db["documents"].create_index(
    "metadata.expires_at",
    name="uploads_ttl",
    expireAfterSeconds=0,   # expira exatamente no timestamp gravado no campo
)
```

**Por que existe:** documento enviado pela tela durante uma demo/reunião é descartável — expira sozinho (`UPLOAD_TTL_HOURS`, padrão 24h). O corpus base (ingerido por CLI) **nunca** grava `metadata.expires_at`, e o monitor de TTL do MongoDB ignora documentos em que o campo indexado está ausente — é essa ausência, e não um campo `is_demo`, que protege o corpus de referência de expirar junto. Essa mesma ausência/presença também é usada como fronteira lógica entre os dois "workspaces" da UI (ver query 8).

---

## Buscas de recuperação (o coração do RAG)

### 5. Busca vetorial — `$vectorSearch`

**Onde:** `agent.py::_vector_pipeline` (linhas 58-79), executada em `agent.py::retrieve_context` (linha 142, dentro de `_run_vector`).

```python
{"$vectorSearch": {
    "index": "vector_index",
    "path": "embedding",
    "queryVector": embedding,          # 1024 floats, voyage-3
    "numCandidates": top_k * 15,       # top_k default 15 -> 225 candidatos
    "limit": top_k,
    "filter": {"$and": [
        {"metadata.client_id": CLIENT_ID},
        {"metadata.nivel_acesso": {"$in": access_levels}},
        {"metadata.source": {"$in": sources}},   # só se a UI restringiu a documentos específicos
    ]},
}},
{"$project": {"text": 1, "metadata": 1, "vector_score": {"$meta": "vectorSearchScore"}}}
```

**Exemplo mascarado de chamada** (pergunta conceitual, sem termo exato):
```python
collection.aggregate([
    {"$vectorSearch": {
        "index": "vector_index", "path": "embedding",
        "queryVector": [0.0123, -0.0456, "... 1024 floats ..."],
        "numCandidates": 225, "limit": 15,
        "filter": {"$and": [
            {"metadata.client_id": "tenant-x"},
            {"metadata.nivel_acesso": {"$in": ["publico"]}},
        ]},
    }},
    {"$project": {"text": 1, "metadata": 1, "vector_score": {"$meta": "vectorSearchScore"}}},
])
```

**O que faz:** encontra os `top_k` chunks mais próximos semanticamente da pergunta (embedding gerado com `voyage-3`), já filtrados por tenant, ACL e (opcionalmente) documentos selecionados na UI.

**Por que existe:** é a metade "semântica" da recuperação — encontra passagens relevantes mesmo quando a pergunta usa palavras diferentes do documento. `numCandidates = top_k * 15` dá folga ao algoritmo (HNSW) para não perder recall antes de aplicar o `limit`.

**Isolamento multi-tenant:** o filtro `metadata.client_id` é comentado no código como "defense in depth" — redundante hoje porque já existe um banco por tenant, mas se vira uma garantia real vale se a topologia mudar para banco compartilhado. Nunca remova esse filtro achando que é morto.

**Resiliência:** se o embedding da pergunta falhar (timeout/erro da VoyageAI), essa busca é pulada inteiramente e o pipeline degrada para lexical-only (ver linha 129-133 de `agent.py`). Se a própria busca vetorial lançar exceção (índice fora, etc.), o erro é logado e o resultado tratado como lista vazia — não derruba o turno.

### 6. Busca lexical — `$search` (Atlas Search / BM25)

**Onde:** `agent.py::_lexical_pipeline` (linhas 82-96), executada em `agent.py::retrieve_context` (linha 149, dentro de `_run_lexical`).

```python
{"$search": {
    "index": "text_index",
    "compound": {
        "must": [{"text": {"query": query, "path": "text"}}],
        "filter": [
            {"in": {"path": "metadata.client_id", "value": [CLIENT_ID]}},
            {"in": {"path": "metadata.nivel_acesso", "value": access_levels}},
            {"in": {"path": "metadata.source", "value": sources}},  # opcional
        ],
    },
}},
{"$limit": top_k},
{"$project": {"text": 1, "metadata": 1, "search_score": {"$meta": "searchScore"}}}
```

**Exemplo mascarado** (pergunta com termo exato, ex. um número de processo/norma):
```python
collection.aggregate([
    {"$search": {"index": "text_index", "compound": {
        "must": [{"text": {"query": "prazo de vigência do plano diretor", "path": "text"}}],
        "filter": [
            {"in": {"path": "metadata.client_id", "value": ["tenant-x"]}},
            {"in": {"path": "metadata.nivel_acesso", "value": ["publico"]}},
        ],
    }}},
    {"$limit": 15},
    {"$project": {"text": 1, "metadata": 1, "search_score": {"$meta": "searchScore"}}},
])
```

**O que faz:** busca textual BM25 sobre `text`, com o mesmo filtro de ACL/tenant/documento do lado vetorial, aplicado dentro de `compound.filter` (não como `$match` posterior).

**Por que existe:** cobre exatamente o que a busca vetorial erra — sigla, número exato, nome próprio. Corpus jurídico/administrativo é feito disso.

**Resiliência:** se falhar, é logada e tratada como lista vazia — o pipeline degrada para vector-only.

### 7. Fusão (RRF) e rerank — não são queries MongoDB, mas fecham o pipeline

**Onde:** `agent.py::retrieve_context`, linhas 154-197.

Não é uma query, é lógica Python: os resultados das duas buscas acima rodam **em paralelo** (`ThreadPoolExecutor`, 2 workers) e são fundidos por Reciprocal Rank Fusion:

```python
entry["rrf"] += 1.0 / (RRF_K + rank)   # RRF_K = 60
entry["matched_by"].add("vetorial" | "léxico")
```

Depois, o conjunto fundido passa por `voyage.rerank(query, documents, model="rerank-2", top_k=min(8, len(documents)))` — top 8 final. `matched_by` é o que alimenta o badge "vetorial"/"léxico" no `Sources` da UI (ver `ui-flows.md`).

**Por que existe:** RRF funde ranqueamentos de escalas diferentes (score vetorial e score BM25 não são comparáveis) sem precisar normalizar. Rerank é, segundo o autor do código, "o estágio que mais melhora a resposta final por token gasto" — separa "8 chunks que casaram" de "os 8 chunks que respondem". Se `rerank-2` falhar, mantém a ordem do RRF (degrada, não quebra).

---

## Sumário/outline do documento (para o system prompt cacheado)

### 8. `$match` + `$group` — outline por página

**Onde:** `backend/api.py::_get_document_outline`, linhas 250-284.

```python
get_client()[DB_NAME]["documents"].aggregate([
    {"$match": {"metadata.nivel_acesso": {"$in": access_levels},
                "metadata.source": {"$in": sources}}},   # segunda condição só se houver sources
    {"$sort": {"metadata.page": 1, "metadata.chunk_id": 1}},
    {"$group": {
        "_id": {"source": "$metadata.source", "page": "$metadata.page"},
        "preview": {"$first": "$text"},
    }},
    {"$sort": {"_id.source": 1, "_id.page": 1}},
    {"$limit": 200},
])
```

**O que faz:** monta um "sumário" (documento + página → início do conteúdo) usado dentro do bloco de instruções do system prompt do Claude.

**Por que existe:** dois motivos. (1) Ajuda o modelo a citar a página certa. (2) É um truque de prompt caching: a Anthropic só efetiva cache de prefixo acima de ~1024 tokens, e o bloco de instruções estáticas sozinho (~120 tokens) nunca chega lá — sem o outline, o `cache_control: ephemeral` seria um no-op silencioso. O outline é estável entre turnos (muda só quando o corpus muda), então empurra o bloco para cima do mínimo e o cache passa a valer de verdade. É cacheado em memória por 1h, com chave `(corpus_version, access_levels, sources)`.

**Atenção de segurança:** a chave do cache **precisa** incluir `access_levels` — sem isso, o outline construído para um chamador `restrito` vazaria (via cache) um preview de páginas restritas para um chamador `publico` seguinte. Isso já foi um bug real do repositório, corrigido.

---

## Biblioteca de documentos (`backend/documents.py`)

### 9. Listagem de documentos com contagem de chunks — `$group` + `$facet`

**Onde:** `backend/documents.py::list_documents`, linhas 99-116.

```python
get_client()[DB_NAME]["documents"].aggregate([
    {"$group": {
        "_id": "$metadata.source",
        "chunks": {"$sum": 1},
        "file": {"$first": "$metadata.file"},
        "expires_at": {"$max": "$metadata.expires_at"},
        "nivel_acesso": {"$addToSet": "$metadata.nivel_acesso"},
    }},
    {"$match": {"_id": {"$ne": None}}},
    {"$facet": {
        "page": [{"$sort": {"_id": 1}}, {"$limit": 200}],   # DOCUMENTS_LIST_LIMIT
        "count": [{"$count": "n"}],
    }},
])
```

**O que faz:** agrupa os chunks por `metadata.source` (um documento = N chunks) e devolve, num único round-trip, a página de resultados (até 200) e a contagem total exata via `$facet` — é o que alimenta o painel "Documentos" da UI (`expiryLabel`, tag `restrito`, tag `base`).

**Por que existe:** `$facet` evita duas idas ao banco (uma para a página, outra para o `$count`) — o `$count` "de graça" é o que permite a UI avisar "há mais documentos do que os 200 mostrados" (`truncated`) em vez de cortar silenciosamente.

### 10. Resolução de workspace (base vs. uploads) — `distinct`

**Onde:** `backend/documents.py::sources_for_scope`, linhas 145-172.

```python
get_client()[DB_NAME]["documents"].distinct(
    "metadata.source",
    {"metadata.expires_at": {"$exists": True}},   # True = uploads da demo; False = corpus base
)
```

**O que faz:** devolve a lista de `source` (nomes de documento) que pertencem a um dos dois workspaces da UI. `scope == "all"` não roda nada, devolve `None` (sem escopo).

**Por que existe:** é a query que implementa a separação das duas abas da tela (`Corpus de referência` vs. `Novo conteúdo`) **sem criar banco, índice ou campo novo** — só reusa o carimbo TTL que já existia para outro motivo (query 4). Cacheado por `(corpus_version, scope)` para não rodar um `distinct` a cada turno de chat.

### 11. `is_protected` — dois `count_documents` para proteger o corpus base

**Onde:** `backend/documents.py::is_protected`, linhas 175-186.

```python
col.count_documents({"metadata.source": source}, limit=1)
col.count_documents({"metadata.source": source, "metadata.expires_at": {"$exists": True}}, limit=1)
```

**O que faz:** um documento é "protegido" (não removível pela UI) se existir mas **nenhum** de seus chunks tiver `metadata.expires_at` — ou seja, foi ingerido por CLI, não por upload.

**Por que existe:** o `DELETE /api/documents/{source}` precisa devolver 403 para o corpus base do tenant (reingeri-lo custa uma hora de embedding com rate limit da VoyageAI). `limit=1` em ambas as chamadas é intencional — é uma checagem de existência, não uma contagem, então não precisa varrer a coleção inteira.

### 12. Remoção de documento — `delete_many` com segunda trava

**Onde:** `backend/documents.py::delete_document`, linhas 189-198.

```python
get_client()[DB_NAME]["documents"].delete_many(
    {"metadata.source": source, "metadata.expires_at": {"$exists": True}}
)
```

**Por que existe:** mesmo depois de `is_protected` já ter barrado a chamada, o próprio `delete_many` só casa chunks que **têm** `expires_at` — uma segunda trava (defesa em profundidade) para que um documento "misto" nunca consiga levar o corpus base junto.

---

## Ingestão (`ingest.py`) — escritas

### 13. `count_documents` — checagem de "já indexado"

**Onde:** `ingest.py:203`

```python
collection.count_documents({"metadata.source": source_name})
```

Se > 0 e `reset=False`, levanta `AlreadyIndexedError` (evita reindexar sem intenção). Se `reset=True`, roda um `delete_many({"metadata.source": source_name})` antes de reingerir; numa ingestão com TTL (upload), o filtro ganha `metadata.expires_at: {$exists: true}` e, se existir chunk permanente com o mesmo `source`, a ingestão levanta `ProtectedSourceError` sem apagar nada. O upload já recusa antes (`documents.start_ingestion` → `is_protected`).

### 14. `insert_many` — gravação dos chunks + embeddings

**Onde:** `ingest.py:45` (nativo) e `ingest.py:295` (clássico)

```python
collection.insert_many(docs_to_insert)
```

onde cada documento tem a forma:

```python
{
    "text": "<texto do chunk>",
    "embedding": [0.0123, -0.0456, "... 1024 floats ..."],
    "metadata": {
        "source": "documento-x",       # nome normalizado do arquivo
        "client_id": "tenant-x",       # tenant — gravado em TODO chunk
        "file": "documento-x.pdf",
        "page": 3,
        "chunk_id": 42,
        "nivel_acesso": "publico",     # ou "restrito"
        # "expires_at": <datetime>,    # só presente em upload de demo
    },
}
```

**Por que existe:** inserção **por lote** (batches de 10, pausa de 22s entre lotes — limite de 3 req/min do tier gratuito da VoyageAI), não tudo de uma vez — se o processo cair no meio, o progresso parcial sobrevive no Atlas em vez de se perder. `metadata.client_id` é o campo que sustenta o filtro de isolamento multi-tenant nas queries 5 e 6.

---

## Conversas (`backend/api.py`)

### 15. `update_one` upsert — persistir turno de conversa

**Onde:** `backend/api.py::_save_conversation`, linhas 319-335.

```python
get_client()[DB_NAME]["conversations"].update_one(
    {"_id": thread_id},
    {"$set": {"client": CLIENT_NAME, "document": DOCUMENT_TITLE,
              "messages": messages, "updated_at": datetime.now(timezone.utc)}},
    upsert=True,
)
```

**Por que existe:** persiste o histórico de conversa no mesmo Atlas (não em Redis/outro serviço) para que `GET /api/history/{thread_id}` retome a conversa após reload de página. Falha de escrita aqui é **engolida de propósito** (try/except silencioso, só loga) — um soluço do banco não pode derrubar o chat em andamento; perder o histórico é aceitável, perder a resposta que está sendo gerada não é.

### 16. `find_one` — carregar conversa

**Onde:** `backend/api.py::_load_conversation`, linhas 338-347.

```python
get_client()[DB_NAME]["conversations"].find_one({"_id": thread_id})
```

Limita ao histórico às últimas `MAX_HISTORY_MESSAGES` (16) mensagens ao devolver.

---

## Verificação de identidade do tenant (`db.py`)

### 17. `find_one` + `insert_one` — write-once, verify-forever

**Onde:** `db.py::verify_tenant_identity`, linhas 40-69.

```python
col = get_client()[db_name]["_meta"]
existing = col.find_one({"_id": "tenant_identity"})
# primeiro boot:
col.insert_one({"_id": "tenant_identity", "client_id": client_id})
# boots seguintes: confere existing["client_id"] == client_id, senão aborta o boot
```

**Por que existe:** pega um `MONGO_URI` reaproveitado apontando para o banco de outro tenant (erro operacional plausível: `.env` copiado de outro cliente). Roda no `startup` do FastAPI — se divergir, o processo nem sobe.

---

## Resumo por arquivo (para achar rápido)

| Arquivo | O que tem lá |
|---|---|
| `setup_db.py` | os 4 índices (vector_index, text_index, updated_at_ttl, uploads_ttl) |
| `agent.py` | `$vectorSearch` + `$search` (recuperação híbrida), RRF, rerank |
| `backend/api.py` | outline (`$match`+`$group`), persistência de conversa (`update_one`/`find_one`) |
| `backend/documents.py` | listagem (`$group`+`$facet`), `distinct` de workspace, `count_documents`/`delete_many` de proteção |
| `ingest.py` | `count_documents` de "já indexado", `delete_many` de reset, `insert_many` dos chunks |
| `db.py` | `find_one`/`insert_one` de identidade do tenant |
