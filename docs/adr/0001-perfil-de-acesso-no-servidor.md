# ADR 0001 — Perfil de acesso decidido no servidor (token assinado)

- Status: aceito (2026-11-12); emenda de 2026-11-13 (restrito sem senha na demo)
- Contexto do código: `backend/auth.py`, `backend/api.py`, `backend/graph_retrieval.py`, `backend/documents.py`

## Contexto

O isolamento entre tenants já era do servidor: `CLIENT_ID` vem do `.env`, é carimbado em todo
chunk e entra como filtro dentro do `$vectorSearch` e do `$search` (pré-filtro, nunca `$match`
depois). O nível de acesso (`publico`/`restrito`), não: o corpo de `/api/chat` dizia
`access_level: "restrito"` e o servidor obedecia.

A avaliação ao vivo de 2026-11-12 (banco `*_test`, tenant B semeado na mesma coleção) mostrou:

1. chamada sem credencial com `access_level: "restrito"` recebeu o trecho restrito no evento `meta`;
2. a aba Graph-RAG não filtrava `nivel_acesso` na evidência nem nas entidades: um upload restrito
   aparecia para o perfil público;
3. `/api/history/{id}` devolvia qualquer conversa;
4. `retrieve_context(access_levels=[])` virava acesso total.

## Decisão

- O nível vem de um token HS256 (`Authorization: Bearer`) com `tid` (tenant), `lvl` e `exp`,
  verificado em `backend/auth.py`. Sem token: só `publico`. Token com assinatura inválida,
  `alg` diferente de HS256, expirado ou de outro tenant: **401** (nunca rebaixa em silêncio).
- `access_level` no corpo continua aceito, mas só **reduz** o que o token concede.
- `POST /api/session` emite o token na demo (o botão de perfil da UI chama esse endpoint). O nível
  público sai sem credencial. O restrito: com `RAG_DEMO_PASSWORD` definida, só com essa senha
  (comparada com `hmac.compare_digest`, 1 s de atraso na falha); sem a variável, sai sem senha
  (emenda de 2026-11-13, abaixo).
  `RAG_DEMO_LOGIN=0` desliga o emissor; em produção o token vem do IdP e só a verificação fica.
- Jobs de upload restrito e remoção de documento seguem o mesmo nível: job restrito some da
  lista e responde 404 por id; `DELETE` exige nível que cubra todos os trechos e responde 404
  igual ao inexistente.
- O pipeline do Graph-RAG exibido na UI troca a lista `$nin` de documentos negados por um
  marcador sem nome nem contagem e marca `pipeline_redacted: true`.
- Upload com nome de documento que o chamador não lê (algum trecho acima do nível concedido)
  responde 404 antes de qualquer escrita, com e sem `reset`; antes, `reset=true` sem token
  apagava os trechos restritos e gravava conteúdo público no lugar. O erro "já indexado" não
  traz mais contagem de trechos. A checagem se repete no worker de ingestão (serializado), conta
  também jobs na fila ou em andamento com o mesmo nome num nível que o chamador não lê, cada job
  grava o arquivo em `<job_id>_<source>` e o `reset` da ingestão só apaga trechos dos níveis
  concedidos (trecho de outro nível recusa o job). Sem isso havia uma corrida: com um upload
  restrito na fila, um upload público com o mesmo nome passava na checagem do request.
- `DELETE` leva os níveis concedidos para dentro do filtro do `delete_many`
  (`documents.delete_document(source, levels)`), e o boot apaga arquivos de job órfãos
  (`<job_id>_*`) em `UPLOAD_DIR` (`documents.cleanup_upload_dir`).
- Com `RAG_DEMO_PASSWORD` definida, `POST /api/session` com senha errada ou ausente espera
  `RAG_DEMO_FAIL_DELAY_S` (1 s) antes do 401.

## Modelo de ameaça: chamador sem token (varredura das rotas que escrevem, 2026-11-12)

| Rota | O que ele consegue | O que não consegue |
|---|---|---|
| `POST /api/session` | token público; **sem `RAG_DEMO_PASSWORD`, também o restrito** (emenda 2026-11-13) | com `RAG_DEMO_PASSWORD`: token restrito sem a senha (1 s por falha) |
| `POST /api/documents` | enviar, reindexar e sobrescrever upload **público** | enviar restrito (403); tocar nome de documento que não lê (404, nada gravado); substituir o corpus base (422) |
| `DELETE /api/documents/{source}` | apagar upload **público** | apagar restrito ou misto (404 igual ao inexistente); corpus base (403) |
| `POST /api/chat` | gravar a própria conversa e o checkpoint do LangGraph sob um `thread_id` | ler conversa restrita; sobrescrever conversa gravada sob nível maior |

Riscos aceitos na PoC (sem identidade de usuário): um chamador sem token pode sobrescrever upload
ou conversa **pública** de outro (o `thread_id` é UUID não adivinhável) e gravar checkpoint num
`thread_id` alheio (não há rota que leia checkpoint). O 404 de upload revela só que **um nome**
está ocupado por algo fora do perfil, sem conteúdo, nível ou contagem.

Metadado aceito: `/api/status` e `/api/health` devolvem a contagem total de trechos do tenant
(todos os níveis). É um número agregado, sem nome nem conteúdo, e o Portal e o smoke dependem
dele; um deploy que trate até a quantidade como sensível deve tirar `chunks` dessas rotas.
- Segredo em `RAG_AUTH_SECRET`; sem ele, cada processo sorteia um segredo efêmero (não existe
  segredo padrão conhecido; tokens morrem no restart).
- O mesmo nível concedido filtra todo caminho de leitura: busca híbrida, outline, Graph-RAG
  (evidência por `nivel_acesso`; entidade cujo `sources` cita documento com trecho negado sai de
  todos os estágios; comunidades saem quando há documento negado), listagem de documentos,
  escopo do workspace e histórico (conversa carimbada com `client_id` + `access_level`).
- Upload `restrito` exige token restrito.

## Emenda de 2026-11-13: restrito sem senha na demo (decisão do usuário)

Decisão: a demo não precisa de senha para o perfil restrito. Sem `RAG_DEMO_PASSWORD`,
`POST /api/session` emite qualquer nível sem senha e a UI troca de perfil sem perguntar
(`/api/config` informa `auth.restricted_requires_password: false`). Com a variável definida,
nada muda: senha checada com `hmac.compare_digest` e 1 s de atraso na falha.

O que continua igual: o nível sempre vem do token verificado no servidor (nunca do corpo), os
filtros por nível em todas as rotas, o isolamento entre tenants (o token carrega o `tid` e o de
outro tenant dá 401), as guardas de upload, delete e jobs, e o segredo de assinatura
(`RAG_AUTH_SECRET`; sem ele, aleatório por processo). Muda só quem pode pedir um token restrito.

Risco aceito, sem rodeio: **sem senha, quem alcança a API consegue o perfil restrito** com um
`curl` e lê todo o conteúdo restrito do tenant. Nessa configuração o nível de acesso demonstra o
mecanismo (filtro no servidor, mesma pergunta com fontes diferentes), não protege nada. Para
proteger a demo, basta definir `RAG_DEMO_PASSWORD`; em produção, `RAG_DEMO_LOGIN=0` e o token
vem do IdP (SSO). O isolamento entre tenants não depende dessa escolha.

## Alternativas consideradas

- **JWT com biblioteca (PyJWT/JWKS):** é o caminho de produção com IdP (RS256 + JWKS). Para a PoC,
  HS256 com `hmac` da stdlib evita dependência nova e cobre o contrato (claims, expiração, tenant).
- **RLS no banco:** o MongoDB não tem RLS nativo; o equivalente é o filtro forçado na camada de
  acesso (o que já existe para o tenant) mais views por tenant. Ver
  https://www.mongodb.com/docs/atlas/atlas-vector-search/multi-tenant-architecture/.
- **Nível por entidade no grafo:** exigiria reconstruir o grafo da demo e mudar o schema; o filtro
  por `sources` negados usa campos já indexados (`kg_vector_index`/`kg_text_index`) e falha fechado.

## Consequências

- A demo continua com o botão público/restrito, agora mediado pelo servidor. Sem
  `RAG_DEMO_PASSWORD` o restrito sai sem senha (emenda de 2026-11-13); com ela, a UI pede a senha.
  Uma senha compartilhada não é identidade: em produção, `RAG_DEMO_LOGIN=0` e o token vem do IdP.
- Conversas antigas sem `access_level` só são lidas por perfil restrito (falha fechada).
- Medição: `eval/isolation_eval.py` (canários de tenant e de nível, injeção em documento,
  credenciais forjadas, listagem, histórico). Regressões offline em `tests/test_access_control.py`.
