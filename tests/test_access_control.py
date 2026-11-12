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
    def test_demo_session_issues_verifiable_token(self):
        resp = TestClient(api.app).post("/api/session", json={"level": "restrito"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(auth.verify_token(resp.json()["token"], TENANT)["lvl"], "restrito")

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
            _, pipeline = graph_retrieval.run_graph(db, "q", access_levels=["publico"])
        ds.assert_called_once_with(["publico"])
        executed = db["kg_entities"].aggregate.call_args[0][0]
        self.assertIn('"$nin": ["R"]', json.dumps(executed))
        # O pipeline exibido na UI não revela o nome do documento negado.
        self.assertNotIn('"R"', json.dumps(pipeline))
        self.assertIn("1 documento(s) fora do perfil", json.dumps(pipeline))


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
