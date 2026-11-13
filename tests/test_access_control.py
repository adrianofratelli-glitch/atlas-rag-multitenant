"""Perfil de acesso decidido no servidor (token assinado) e ACL em todo caminho de leitura.

Achados da avaliação ao vivo de 2026-11-12 (banco *_test, scripts/isolation_eval.py):
  1. `access_level: "restrito"` no corpo do chat, sem credencial, devolvia o trecho restrito.
  2. A evidência do Graph-RAG não filtrava `nivel_acesso`: upload restrito vazava pela aba Graph.
  3. `/api/history/{id}` devolvia conversa restrita a qualquer chamador com o thread_id.
  4. `retrieve_context(access_levels=[])` virava acesso total (falha aberta).
Cada teste falha se a correção for revertida. Sem rede: Atlas e LLM simulados.
"""
import base64
import json
import os
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("MONGO_URI", "mongodb://localhost/test")
os.environ.setdefault("VOYAGE_API_KEY", "test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("CLIENT_ID", "test-tenant")
os.environ["RAG_NATIVE"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from backend import agent, api, auth, documents, graph_retrieval  # noqa: E402

TENANT = api.CLIENT_ID


def _b64(obj) -> str:
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()


class TokenTests(unittest.TestCase):
    def test_roundtrip(self):
        claims = auth.verify_token(auth.issue_token(TENANT, "restrito"), TENANT)
        self.assertEqual((claims["tid"], claims["lvl"]), (TENANT, "restrito"))

    def test_tampered_level_is_rejected(self):
        h, p, s = auth.issue_token(TENANT, "publico").split(".")
        claims = json.loads(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4)))
        claims["lvl"] = "restrito"
        with self.assertRaises(auth.AuthError):
            auth.verify_token(f"{h}.{_b64(claims)}.{s}", TENANT)

    def test_alg_none_is_rejected(self):
        claims = {"sub": "x", "tid": TENANT, "lvl": "restrito", "exp": int(time.time()) + 60}
        with self.assertRaises(auth.AuthError):
            auth.verify_token(f"{_b64({'alg': 'none'})}.{_b64(claims)}.", TENANT)

    def test_expired_token_is_rejected(self):
        tok = auth.issue_token(TENANT, "restrito", ttl_s=1)
        with mock.patch.object(auth.time, "time", return_value=time.time() + 120):
            with self.assertRaises(auth.AuthError):
                auth.verify_token(tok, TENANT)

    def test_token_of_another_tenant_is_rejected(self):
        """Mesmo segredo em dois deploys: o token do tenant B não abre o tenant A."""
        with self.assertRaises(auth.AuthError):
            auth.verify_token(auth.issue_token("outro-tenant", "restrito"), TENANT)

    def test_garbage_and_wrong_scheme(self):
        for value in ("abc", "a.b", "a.b.c.d", "...", "Bearer"):
            with self.subTest(value=value), self.assertRaises(auth.AuthError):
                auth.granted_level(value if value == "Bearer" else f"Bearer {value}", TENANT)
        with self.assertRaises(auth.AuthError):
            auth.granted_level("Basic dXNlcjpwYXNz", TENANT)

    def test_no_token_is_public(self):
        self.assertEqual(auth.granted_level(None, TENANT), "publico")

    def test_request_can_only_lower_the_grant(self):
        self.assertEqual(auth.effective_level("publico", "restrito"), "publico")
        self.assertEqual(auth.effective_level("restrito", "publico"), "publico")
        self.assertEqual(auth.effective_level("restrito", None), "restrito")
        self.assertEqual(auth.effective_level("publico", "admin"), "publico")

    def test_legacy_record_without_level_requires_highest(self):
        self.assertFalse(auth.can_read("publico", None))
        self.assertTrue(auth.can_read("restrito", None))
        self.assertTrue(auth.can_read("restrito", "publico"))
        self.assertFalse(auth.can_read("publico", "restrito"))


class ChatAccessTests(unittest.TestCase):
    """O nível que chega ao retrieval vem do token, nunca do corpo."""

    def _chat(self, body, headers=None):
        seen = {}

        def fake_run_turn(**kw):
            seen["levels"] = kw["access_levels"]
            kw["emit"]({"type": "done"})
            return {"refused": True}

        client = TestClient(api.app)
        with mock.patch.object(api.rag_graph, "run_turn", fake_run_turn), \
             mock.patch.object(api, "get_status", return_value=(True, 1)), \
             mock.patch.object(api, "_get_document_outline", return_value=""), \
             mock.patch.object(api, "_get_llm", return_value=mock.MagicMock()):
            resp = client.post("/api/chat", json=body, headers=headers or {})
        return resp, seen.get("levels")

    def test_body_claiming_restrito_without_token_gets_public_only(self):
        resp, levels = self._chat({"question": "orçamento sigiloso", "access_level": "restrito"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(levels, ["publico"])

    def test_restrito_token_unlocks_restricted(self):
        tok = auth.issue_token(TENANT, "restrito")
        _, levels = self._chat({"question": "q"}, {"Authorization": f"Bearer {tok}"})
        self.assertEqual(levels, ["publico", "restrito"])

    def test_restrito_token_with_public_request_stays_public(self):
        tok = auth.issue_token(TENANT, "restrito")
        _, levels = self._chat({"question": "q", "access_level": "publico"},
                               {"Authorization": f"Bearer {tok}"})
        self.assertEqual(levels, ["publico"])

    def test_forged_or_foreign_token_is_401_not_silent_downgrade(self):
        for tok in ("x.y.z", auth.issue_token("outro-tenant", "restrito")):
            with self.subTest(tok=tok[:12]):
                resp, levels = self._chat({"question": "q"}, {"Authorization": f"Bearer {tok}"})
                self.assertEqual(resp.status_code, 401)
                self.assertIsNone(levels)


class SessionAndHistoryTests(unittest.TestCase):
    def _session(self, body, password_env):
        with mock.patch.object(auth, "DEMO_PASSWORD", password_env), \
             mock.patch.object(api, "DEMO_FAIL_DELAY_S", 0.0):
            return TestClient(api.app).post("/api/session", json=body)

    def test_failed_session_is_delayed_and_success_is_not(self):
        with mock.patch.object(auth, "DEMO_PASSWORD", "s3nha"), \
             mock.patch.object(api.asyncio, "sleep", new=mock.AsyncMock()) as sleep:
            client = TestClient(api.app)
            self.assertEqual(client.post("/api/session", json={"level": "restrito", "password": "x"}).status_code, 401)
            sleep.assert_called_once_with(api.DEMO_FAIL_DELAY_S)
            sleep.reset_mock()
            self.assertEqual(client.post("/api/session", json={"level": "restrito", "password": "s3nha"}).status_code, 200)
            sleep.assert_not_called()
        self.assertGreaterEqual(api.DEMO_FAIL_DELAY_S, 1.0)

    def test_public_session_needs_no_password(self):
        resp = self._session({"level": "publico"}, "")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(auth.verify_token(resp.json()["token"], TENANT)["lvl"], "publico")

    def test_restricted_session_without_configured_password_is_open(self):
        """Decisão do usuário (2026-11-13): sem RAG_DEMO_PASSWORD o restrito sai sem senha."""
        for body in ({"level": "restrito"}, {"level": "restrito", "password": ""},
                     {"level": "restrito", "password": "qualquer"}):
            with self.subTest(body=body):
                resp = self._session(body, "")
                self.assertEqual(resp.status_code, 200)
                self.assertEqual(auth.verify_token(resp.json()["token"], TENANT)["lvl"], "restrito")

    def test_open_restricted_token_is_still_tenant_bound_and_verified(self):
        """Sem senha muda só quem pede o token: o de outro tenant segue 401 e o nível vem do token."""
        tok = self._session({"level": "restrito"}, "").json()["token"]
        with self.assertRaises(auth.AuthError):
            auth.verify_token(tok, "outro-tenant")
        self.assertEqual(auth.effective_level(auth.verify_token(tok, TENANT)["lvl"], "publico"), "publico")

    def test_restricted_session_with_wrong_password_is_401(self):
        for pw in (None, "", "errada", "s3nha-demo "):
            with self.subTest(pw=pw):
                resp = self._session({"level": "restrito", "password": pw}, "s3nha-demo")
                self.assertEqual(resp.status_code, 401)
                self.assertNotIn("token", resp.json())

    def test_restricted_session_with_right_password_issues_token(self):
        resp = self._session({"level": "restrito", "password": "s3nha-demo"}, "s3nha-demo")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(auth.verify_token(resp.json()["token"], TENANT)["lvl"], "restrito")

    def test_config_says_whether_restricted_needs_password(self):
        for pw, needs in (("", False), ("s3nha", True)):
            with self.subTest(pw=pw), mock.patch.object(auth, "DEMO_PASSWORD", pw), \
                    mock.patch.object(api, "get_graph_status", return_value={"ready": False}):
                cfg = TestClient(api.app).get("/api/config").json()
            self.assertTrue(cfg["auth"]["restricted_available"])
            self.assertEqual(cfg["auth"]["restricted_requires_password"], needs)

    def test_demo_session_can_be_turned_off(self):
        with mock.patch.object(auth, "DEMO_LOGIN", False):
            resp = TestClient(api.app).post("/api/session", json={"level": "restrito"})
        self.assertEqual(resp.status_code, 404)

    def _history(self, stored, headers=None):
        col = mock.MagicMock()
        col.find_one.return_value = stored
        with mock.patch.object(api, "get_client", return_value={api.DB_NAME: {"conversations": col}}):
            resp = TestClient(api.app).get("/api/history/6f1c1d0e-0000-4000-8000-000000000001",
                                           headers=headers or {})
        return resp, col.find_one.call_args[0][0] if col.find_one.called else None

    def test_restricted_conversation_hidden_from_public(self):
        stored = {"client_id": TENANT, "access_level": "restrito",
                  "messages": [{"role": "assistant", "content": "SEGREDO"}]}
        resp, flt = self._history(stored)
        self.assertEqual(resp.json()["messages"], [])
        self.assertEqual(flt["client_id"], {"$in": [TENANT, None]})
        tok = auth.issue_token(TENANT, "restrito")
        resp, _ = self._history(stored, {"Authorization": f"Bearer {tok}"})
        self.assertEqual(resp.json()["messages"][0]["content"], "SEGREDO")

    def test_conversation_is_stamped_with_tenant_and_level(self):
        col = mock.MagicMock()
        col.find_one.return_value = None
        with mock.patch.object(api, "get_client", return_value={api.DB_NAME: {"conversations": col}}):
            api._save_conversation("t1", [{"role": "user", "content": "q"}], "restrito")
        flt, update = col.update_one.call_args[0]
        self.assertEqual(flt["client_id"], {"$in": [TENANT, None]})
        self.assertEqual((update["$set"]["client_id"], update["$set"]["access_level"]), (TENANT, "restrito"))

    def test_public_caller_cannot_overwrite_restricted_thread(self):
        col = mock.MagicMock()
        col.find_one.return_value = {"access_level": "restrito"}
        with mock.patch.object(api, "get_client", return_value={api.DB_NAME: {"conversations": col}}):
            api._save_conversation("t1", [{"role": "user", "content": "q"}], "publico")
        col.update_one.assert_not_called()

    def test_job_listing_and_job_lookup_hide_restricted_uploads(self):
        jobs = [{"job_id": "j1", "source": "pub", "nivel_acesso": "publico"},
                {"job_id": "j2", "source": "segredo", "nivel_acesso": "restrito"}]
        with mock.patch.object(documents, "list_documents",
                               return_value={"documents": [], "truncated": False, "total": 0}), \
             mock.patch.object(documents, "list_jobs", return_value=jobs), \
             mock.patch.object(documents, "get_job", side_effect=lambda j: next(x for x in jobs if x["job_id"] == j)):
            client = TestClient(api.app)
            listed = [j["source"] for j in client.get("/api/documents").json()["jobs"]]
            self.assertEqual(listed, ["pub"])
            self.assertEqual(client.get("/api/documents/jobs/j2").status_code, 404)
            self.assertEqual(client.get("/api/documents/jobs/j1").status_code, 200)
            tok = auth.issue_token(TENANT, "restrito")
            self.assertEqual(client.get("/api/documents/jobs/j2",
                                        headers={"Authorization": f"Bearer {tok}"}).status_code, 200)

    def test_delete_restricted_without_grant_looks_like_missing(self):
        def levels(source):
            return {"segredo": ["restrito"], "misto": ["publico", "restrito"], "pub": ["publico"]}.get(source, [])

        with mock.patch.object(documents, "source_levels", side_effect=levels), \
             mock.patch.object(documents, "delete_document", return_value=3) as dele:
            client = TestClient(api.app)
            for src in ("segredo", "misto", "nao-existe"):
                with self.subTest(src=src):
                    resp = client.delete(f"/api/documents/{src}")
                    self.assertEqual((resp.status_code, resp.json()["detail"]), (404, "documento não encontrado"))
            dele.assert_not_called()
            self.assertEqual(client.delete("/api/documents/pub").status_code, 200)
            tok = auth.issue_token(TENANT, "restrito")
            self.assertEqual(client.delete("/api/documents/misto",
                                           headers={"Authorization": f"Bearer {tok}"}).status_code, 200)

    def test_source_levels_treats_missing_level_as_restricted(self):
        col = mock.MagicMock()
        col.distinct.return_value = ["publico"]
        col.count_documents.return_value = 1
        with mock.patch.object(documents, "get_client", return_value={documents.DB_NAME: {"documents": col}}):
            self.assertEqual(documents.source_levels("x"), ["publico", "restrito"])

    def _upload(self, levels, reset, headers=None, name="segredo.md"):
        with mock.patch.object(documents, "source_levels", return_value=levels), \
             mock.patch.object(documents, "is_protected", return_value=False), \
             mock.patch.object(documents, "_executor") as ex, \
             mock.patch.object(Path, "write_bytes") as wb:
            resp = TestClient(api.app).post(
                "/api/documents", files={"file": (name, b"# conteudo publico")},
                data={"nivel_acesso": "publico", "reset": str(reset).lower()}, headers=headers or {})
        return resp, ex, wb

    def test_public_upload_cannot_overwrite_or_probe_restricted_name(self):
        """Achado da revisão 2: reset=true sem token apagava o upload restrito; sem reset, o erro
        do job confirmava nome e contagem."""
        details = set()
        for levels in (["restrito"], ["publico", "restrito"]):
            for reset in (True, False):
                with self.subTest(levels=levels, reset=reset):
                    resp, ex, wb = self._upload(levels, reset)
                    self.assertEqual(resp.status_code, 404)
                    ex.submit.assert_not_called()
                    wb.assert_not_called()
                    details.add(resp.json()["detail"])
        self.assertEqual(len(details), 1)  # mesma resposta com e sem reset

    def test_upload_over_readable_name_still_works(self):
        resp, ex, _ = self._upload(["publico"], True)
        self.assertEqual(resp.status_code, 200)
        ex.submit.assert_called_once()
        tok = auth.issue_token(TENANT, "restrito")
        resp, ex, _ = self._upload(["restrito"], True, {"Authorization": f"Bearer {tok}"})
        self.assertEqual(resp.status_code, 200)
        resp, ex, _ = self._upload([], False)  # nome novo
        self.assertEqual(resp.status_code, 200)

    def test_already_indexed_error_has_no_chunk_count(self):
        from backend.ingest import AlreadyIndexedError

        documents._jobs["jx"] = {"job_id": "jx", "status": "queued"}
        with mock.patch.object(documents, "ingest", side_effect=AlreadyIndexedError("doc", 7)):
            documents._run_job("jx", "/tmp/x.md", "doc", "publico", False)
        err = documents.get_job("jx")["error"]
        documents._jobs.pop("jx", None)
        self.assertIn("já está indexado", err)
        self.assertNotIn("7", err)

    def test_restricted_upload_requires_restricted_token(self):
        with mock.patch.object(documents, "start_ingestion") as start:
            resp = TestClient(api.app).post("/api/documents", files={"file": ("x.md", b"# x")},
                                            data={"nivel_acesso": "restrito"})
        self.assertEqual(resp.status_code, 403)
        start.assert_not_called()

    def test_document_listing_is_filtered_by_grant(self):
        with mock.patch.object(documents, "list_documents",
                               return_value={"documents": [], "truncated": False, "total": 0}) as ld, \
             mock.patch.object(documents, "list_jobs", return_value=[]):
            TestClient(api.app).get("/api/documents")
        ld.assert_called_once_with(["publico"])

    def test_list_documents_pipeline_drops_docs_with_denied_chunks(self):
        seen = []

        class Col:
            def aggregate(self, pipeline):
                seen.append(pipeline)
                return iter([{"page": [], "count": []}])

        with mock.patch.object(documents, "get_client", return_value={documents.DB_NAME: {"documents": Col()}}):
            documents.list_documents(["publico"])
        stages = json.dumps(seen[0])
        self.assertIn('"$elemMatch": {"$nin": ["publico"]}', stages)


class GraphAclTests(unittest.TestCase):
    def test_graph_evidence_filters_access_level(self):
        p = graph_retrieval.build_graph_pipeline("q", access_levels=["publico"], scope="uploads")
        lookup = next(s["$lookup"] for s in p if "$lookup" in s and s["$lookup"]["from"] == "documents")
        self.assertEqual(lookup["pipeline"][0]["$match"]["metadata.nivel_acesso"], {"$in": ["publico"]})

    def test_denied_sources_are_excluded_from_every_entity_stage(self):
        p = graph_retrieval.build_graph_pipeline("q", access_levels=["publico"], scope="uploads",
                                                 denied_sources=["UP_RESTRITO"])
        pipes = p[0]["$rankFusion"]["input"]["pipelines"]
        vs_filter = pipes["vector"][0]["$vectorSearch"]["filter"]
        self.assertIn({"sources": {"$nin": ["UP_RESTRITO"]}}, vs_filter["$and"])
        self.assertEqual(pipes["lexical"][0]["$search"]["compound"]["mustNot"],
                         [{"in": {"path": "sources", "value": ["UP_RESTRITO"]}}])
        hop1 = next(s["$lookup"] for s in p if "$lookup" in s and s["$lookup"]["from"] == "kg_entities")
        self.assertEqual(hop1["pipeline"][0]["$match"]["sources"], {"$nin": ["UP_RESTRITO"]})
        gl = next(s["$graphLookup"] for s in p if "$graphLookup" in s)
        self.assertEqual(gl["restrictSearchWithMatch"]["sources"], {"$nin": ["UP_RESTRITO"]})

    def test_communities_dropped_when_profile_has_denied_documents(self):
        p = graph_retrieval.build_graph_pipeline("q", access_levels=["publico"], scope="base",
                                                 denied_sources=["BASE_RESTRITO"])
        self.assertFalse(any(s.get("$unionWith", {}).get("coll") == "kg_communities" for s in p))
        p = graph_retrieval.build_graph_pipeline("q", access_levels=["publico"], scope="base")
        self.assertTrue(any(s.get("$unionWith", {}).get("coll") == "kg_communities" for s in p))

    def test_run_graph_computes_denied_sources_from_profile(self):
        db = {"kg_entities": mock.MagicMock()}
        db["kg_entities"].aggregate.return_value = []
        with mock.patch.object(documents, "denied_sources", return_value=["R"]) as ds:
            parsed, pipeline = graph_retrieval.run_graph(db, "q", access_levels=["publico"])
        ds.assert_called_once_with(["publico"])
        executed = db["kg_entities"].aggregate.call_args[0][0]
        self.assertIn('"$nin": ["R"]', json.dumps(executed))
        # O pipeline exibido na UI não revela o nome do documento negado.
        self.assertNotIn('"R"', json.dumps(pipeline))
        self.assertIn("<documentos fora do perfil>", json.dumps(pipeline))
        self.assertTrue(parsed["pipeline_redacted"])

    def test_pipeline_not_flagged_when_nothing_denied(self):
        db = {"kg_entities": mock.MagicMock()}
        db["kg_entities"].aggregate.return_value = []
        with mock.patch.object(documents, "denied_sources", return_value=[]):
            parsed, _ = graph_retrieval.run_graph(db, "q", access_levels=["publico", "restrito"])
        self.assertFalse(parsed["pipeline_redacted"])


class BuildGraphNewTenant(unittest.TestCase):
    def test_ensure_indexes_creates_communities_collection_through_helper(self):
        """Tenant novo: o primeiro upload falhava com NamespaceNotFound em kg_communities."""
        from backend import build_graph

        db = mock.MagicMock()
        with mock.patch.object(build_graph, "_ensure_collection") as ensure, \
             mock.patch.object(build_graph, "_ensure_search_index"):
            build_graph.ensure_indexes(db)
        ensure.assert_any_call(db, "kg_communities", build_graph.COMMUNITY_SCHEMA)
        self.assertEqual(ensure.call_args_list[0].args[1], "kg_communities")


class RetrieveContextDefaultDeny(unittest.TestCase):
    def test_empty_levels_mean_public_not_everything(self):
        seen = {}

        def fake_retrieve(query, top_k, levels, *a, **k):
            seen["levels"] = levels
            return "ctx", [], {}

        with mock.patch.object(agent, "_retrieve", fake_retrieve):
            agent.retrieve_context("q", access_levels=[])
        self.assertEqual(seen["levels"], ["publico"])


if __name__ == "__main__":
    unittest.main()


class UploadRaceTests(unittest.TestCase):
    """Revisão 3 (TOCTOU): checar nível só no request deixava uma janela. Com um upload restrito
    na fila, um chamador sem token mandava o mesmo nome com reset=true: sobrescrevia o arquivo
    em disco (o job restrito gravava o conteúdo dele como restrito) ou o job dele apagava os
    trechos restritos (o reset não filtrava nível)."""

    def setUp(self):
        self.store = []  # trechos gravados: {"source", "nivel", "text"}
        self.queue = []
        self.jobs_backup = dict(documents._jobs)
        documents._jobs.clear()

    def tearDown(self):
        documents._jobs.clear()
        documents._jobs.update(self.jobs_backup)

    def _levels(self, source):
        return sorted({c["nivel"] for c in self.store if c["source"] == source})

    def _fake_ingest(self, path, reset=False, nivel_acesso="publico", source_name=None,
                     reset_levels=None, **kw):
        from backend.ingest import AlreadyIndexedError, ProtectedSourceError

        mine = [c for c in self.store if c["source"] == source_name]
        if reset_levels is not None and any(c["nivel"] not in reset_levels for c in mine):
            raise ProtectedSourceError(source_name)
        if mine and not reset:
            raise AlreadyIndexedError(source_name, len(mine))
        self.store[:] = [c for c in self.store if c["source"] != source_name]
        self.store.append({"source": source_name, "nivel": nivel_acesso,
                           "text": Path(path).read_bytes().decode()})
        return {"chunks": 1, "expires_at": None}

    def _patches(self):
        ex = mock.MagicMock()
        ex.submit.side_effect = lambda fn, *a: self.queue.append((fn, a))
        return [mock.patch.object(documents, "_executor", ex),
                mock.patch.object(documents, "source_levels", side_effect=self._levels),
                mock.patch.object(documents, "is_protected", return_value=False),
                mock.patch.object(documents, "ingest", side_effect=self._fake_ingest),
                mock.patch.object(documents, "wait_until_searchable", return_value=True),
                mock.patch.object(documents, "GRAPH_ON_UPLOAD", False),
                mock.patch.object(documents, "_bump_corpus_version")]

    def _run(self, fn):
        import contextlib
        import tempfile

        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as st:
            st.enter_context(mock.patch.object(documents, "UPLOAD_DIR", Path(tmp)))
            for p in self._patches():
                st.enter_context(p)
            return fn()

    def test_pending_restricted_job_blocks_public_upload_of_same_name(self):
        def scenario():
            documents.start_ingestion("anexo.md", b"SEGREDO", nivel_acesso="restrito",
                                      granted_level="restrito")
            with self.assertRaises(documents.SourceNotAccessibleError):
                documents.start_ingestion("anexo.md", b"publico", nivel_acesso="publico", reset=True)
            for fn, args in self.queue:
                fn(*args)
        self._run(scenario)
        self.assertEqual(self.store, [{"source": "anexo", "nivel": "restrito", "text": "SEGREDO"}])

    def test_worker_rechecks_and_restricted_survives_two_queued_jobs(self):
        """Os dois jobs entram na fila (a checagem do request é contornada de propósito); o
        worker roda o restrito, depois o público com reset: o restrito sobrevive."""
        def scenario():
            documents.start_ingestion("anexo.md", b"SEGREDO", nivel_acesso="restrito",
                                      granted_level="restrito")
            with mock.patch.object(documents, "_source_writable", return_value=True):
                pub = documents.start_ingestion("anexo.md", b"publico", nivel_acesso="publico", reset=True)
            paths = [args[1] for _, args in self.queue]
            self.assertEqual(len(set(paths)), 2)  # arquivo por job: um não sobrescreve o outro
            for fn, args in self.queue:
                fn(*args)
            return documents.get_job(pub["job_id"])
        job = self._run(scenario)
        self.assertEqual(self.store, [{"source": "anexo", "nivel": "restrito", "text": "SEGREDO"}])
        self.assertEqual((job["status"], job["error"]), ("error", documents.SOURCE_UNAVAILABLE))
        self.assertNotIn("granted_level", job)

    def test_ingest_reset_never_deletes_levels_outside_the_grant(self):
        from backend import ingest as ingest_mod

        col = mock.MagicMock()
        col.count_documents.side_effect = lambda flt, **k: 1  # existe e há trecho fora do perfil
        with mock.patch.object(ingest_mod, "get_client", return_value={ingest_mod.DB_NAME: {"documents": col}}), \
             mock.patch.object(Path, "exists", return_value=True):
            with self.assertRaises(ingest_mod.ProtectedSourceError):
                ingest_mod.ingest("x.md", reset=True, ttl_hours=24, verbose=False, reset_levels=["publico"])
        col.delete_many.assert_not_called()


class DeleteAndUploadDirTests(unittest.TestCase):
    def test_delete_many_filter_carries_the_callers_levels(self):
        col = mock.MagicMock()
        col.delete_many.return_value.deleted_count = 1
        col.count_documents.return_value = 1  # sobrou trecho de outro nível
        with mock.patch.object(documents, "is_protected", return_value=False), \
             mock.patch.object(documents, "get_client", return_value={documents.DB_NAME: {"documents": col}}), \
             mock.patch.object(documents, "GRAPH_ON_UPLOAD", True), \
             mock.patch.object(documents, "delete_upload_graph") as dg, \
             mock.patch.object(documents, "_bump_corpus_version"):
            documents.delete_document("up", ["publico"])
        flt = col.delete_many.call_args[0][0]
        self.assertEqual(flt["metadata.nivel_acesso"], {"$in": ["publico"]})
        self.assertEqual(flt["metadata.client_id"], documents.CLIENT_ID)
        dg.assert_not_called()  # grafo do upload fica enquanto houver trecho do documento

    def test_api_delete_passes_granted_levels(self):
        with mock.patch.object(documents, "source_levels", return_value=["publico"]), \
             mock.patch.object(documents, "delete_document", return_value=1) as dele:
            TestClient(api.app).delete("/api/documents/pub")
        dele.assert_called_once_with("pub", ["publico"])

    def test_boot_cleanup_removes_only_job_files(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            job = d / f"{'a' * 32}_anexo.md"
            keep = d / "notas.md"
            job.write_text("x")
            keep.write_text("y")
            with mock.patch.object(documents, "UPLOAD_DIR", d):
                self.assertEqual(documents.cleanup_upload_dir(), 1)
            self.assertFalse(job.exists())
            self.assertTrue(keep.exists())
