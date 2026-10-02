"""Recuperação nativa no Atlas (autoEmbed + $rankFusion + $rerank): lógica pura, sem rede."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("MONGO_URI", "mongodb://localhost/test")
os.environ.setdefault("VOYAGE_API_KEY", "test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("CLIENT_ID", "test-tenant")

import config
import native_retrieval as nr
from config import CLIENT_ID


class TestDbGuard(unittest.TestCase):
    def test_test_db_allowed(self):
        config.assert_writable_db("rag_x_native_test")

    def test_demo_db_refused_without_flag(self):
        os.environ.pop("ALLOW_DEMO_DB_WRITE", None)
        with self.assertRaises(RuntimeError):
            config.assert_writable_db("rag_x")

    def test_demo_db_allowed_with_flag(self):
        os.environ["ALLOW_DEMO_DB_WRITE"] = "1"
        try:
            config.assert_writable_db("rag_x")
        finally:
            os.environ.pop("ALLOW_DEMO_DB_WRITE")


def _stage(pipe, name):
    return next(s for s in pipe if name in s)


def _names(pipe):
    return [next(iter(s)) for s in pipe]


class TestBuilder(unittest.TestCase):
    def test_shape_hybrid_rerank(self):
        p = nr.build_native_pipeline("o que é governança?", 15, ["publico"], None, 8)
        names = _names(p)
        self.assertEqual(names[0], "$rankFusion")
        self.assertIn("$rerank", names)
        self.assertEqual(names[-1], "$project")
        self.assertEqual(_stage(p, "$limit")["$limit"], 8)

    def test_client_id_filter_in_both_branches(self):
        p = nr.build_native_pipeline("q", 15, ["publico"], None, 8)
        pipes = p[0]["$rankFusion"]["input"]["pipelines"]
        vs_filter = pipes["vector"][0]["$vectorSearch"]["filter"]
        self.assertIn({"metadata.client_id": CLIENT_ID}, vs_filter["$and"])
        lex_filter = pipes["lexical"][0]["$search"]["compound"]["filter"]
        self.assertIn({"in": {"path": "metadata.client_id", "value": [CLIENT_ID]}}, lex_filter)

    def test_acl_and_sources_in_both_branches(self):
        p = nr.build_native_pipeline("q", 15, ["publico"], ["doc-a"], 8)
        pipes = p[0]["$rankFusion"]["input"]["pipelines"]
        vs = pipes["vector"][0]["$vectorSearch"]["filter"]["$and"]
        self.assertIn({"metadata.nivel_acesso": {"$in": ["publico"]}}, vs)
        self.assertIn({"metadata.source": {"$in": ["doc-a"]}}, vs)
        lex = pipes["lexical"][0]["$search"]["compound"]["filter"]
        self.assertIn({"in": {"path": "metadata.nivel_acesso", "value": ["publico"]}}, lex)
        self.assertIn({"in": {"path": "metadata.source", "value": ["doc-a"]}}, lex)

    def test_no_lexical_branch_when_disabled(self):
        p = nr.build_native_pipeline("q", 15, ["publico"], None, 8, use_lexical=False)
        self.assertEqual(list(p[0]["$rankFusion"]["input"]["pipelines"]), ["vector"])

    def test_no_rerank_stage_when_disabled(self):
        p = nr.build_native_pipeline("q", 15, ["publico"], None, 8, use_rerank=False)
        self.assertNotIn("$rerank", _names(p))

    def test_hostile_query_stays_a_plain_string(self):
        q = '{"$where": "x"} $set {} "aspas" \\'
        p = nr.build_native_pipeline(q, 15, ["publico"], None, 8)
        self.assertEqual(p[0]["$rankFusion"]["input"]["pipelines"]["vector"][0]["$vectorSearch"]["query"], q)
        self.assertEqual(_stage(p, "$rerank")["$rerank"]["query"]["text"], q)

    def test_rerank_guards_missing_text(self):
        p = nr.build_native_pipeline("q", 15, ["publico"], None, 8)
        names = _names(p)
        self.assertLess(names.index("$match"), names.index("$rerank"))
        self.assertEqual(_stage(p, "$match")["$match"], {"text": {"$type": "string", "$ne": ""}})

    def test_mutation_removing_client_filter_is_detectable(self):
        p = nr.build_native_pipeline("q", 15, ["publico"], None, 8)
        vs = p[0]["$rankFusion"]["input"]["pipelines"]["vector"][0]["$vectorSearch"]
        vs["filter"]["$and"] = [c for c in vs["filter"]["$and"] if "metadata.client_id" not in c]
        self.assertNotIn({"metadata.client_id": CLIENT_ID}, vs["filter"]["$and"])


class TestScoreDetails(unittest.TestCase):
    SD = {"value": 0.03, "details": [
        {"inputPipelineName": "vector", "rank": 2, "weight": 1, "value": 0.016},
        {"inputPipelineName": "lexical", "rank": 1, "weight": 1, "value": 0.016}]}

    def test_both(self):
        self.assertEqual(nr.matched_by(self.SD), ["léxico", "vetorial"])

    def test_vector_only(self):
        self.assertEqual(nr.matched_by({"details": [self.SD["details"][0]]}), ["vetorial"])

    def test_missing_or_garbage(self):
        self.assertEqual(nr.matched_by(None), [])
        self.assertEqual(nr.matched_by({}), [])
        self.assertEqual(nr.matched_by({"details": "x"}), [])

    def test_rank(self):
        self.assertEqual(nr.pipeline_rank(self.SD, "vector"), 2)
        self.assertIsNone(nr.pipeline_rank(None, "vector"))


class FakeCol:
    def __init__(self, rows=None, fail_on=None):
        self.rows, self.fail_on, self.calls = rows or [], fail_on, []

    def aggregate(self, pipeline):
        self.calls.append(pipeline)
        if self.fail_on and self.fail_on in _names(pipeline):
            raise RuntimeError("boom")
        return list(self.rows)


ROW = {"_id": "c1", "text": "texto", "metadata": {"source": "doc", "page": 0, "nivel_acesso": "publico"},
       "fusion_score": 0.03, "rerank_score": 0.91,
       "score_details": {"details": [{"inputPipelineName": "vector", "rank": 1, "value": 0.857}]}}


class TestRunNative(unittest.TestCase):
    def test_maps_row_to_internal_shape(self):
        res, info = nr.run_native(FakeCol([ROW]), "q", 15, ["publico"], None, 8)
        self.assertEqual(res[0]["chunk_id"], "c1")
        self.assertEqual(res[0]["rerank_score"], 0.91)
        self.assertEqual(res[0]["matched_by"], {"vetorial"})
        self.assertEqual(res[0]["vector_score"], 0.857)
        self.assertFalse(info["degraded"])

    def test_rerank_failure_degrades_to_lexical_only(self):
        col = FakeCol([ROW], fail_on="$rerank")
        res, info = nr.run_native(col, "q", 15, ["publico"], None, 8)
        self.assertTrue(info["degraded"])
        self.assertEqual(len(col.calls), 2)
        self.assertIn("$search", col.calls[1][0])
        self.assertEqual(res[0]["matched_by"], {"léxico"})

    def test_total_failure_returns_empty_not_raise(self):
        class Dead:
            def aggregate(self, p):
                raise RuntimeError("down")
        res, info = nr.run_native(Dead(), "q", 15, ["publico"], None, 8)
        self.assertEqual(res, [])
        self.assertTrue(info["degraded"])


class TestAgentNative(unittest.TestCase):
    def _patched(self, rows, **extra):
        import agent
        from unittest import mock
        col = FakeCol(rows)
        stack = [mock.patch.object(agent, "NATIVE_ENABLED", True),
                 mock.patch.object(agent, "get_client", return_value={agent.DB_NAME: {"documents": col}}),
                 mock.patch.object(agent, "_get_voyage", side_effect=AssertionError("SDK Voyage usado"))]
        return agent, stack

    def test_flag_on_keeps_contract_and_skips_voyage_sdk(self):
        agent, stack = self._patched([ROW])
        with stack[0], stack[1], stack[2]:
            ctx, sources, stats = agent.retrieve_context("q", access_levels=["publico"])
        self.assertTrue(stats["native"])
        self.assertEqual(stats["embed_model"], "voyage-4")
        self.assertEqual(sources[0]["matched_by"], ["vetorial"])
        self.assertEqual(sources[0]["rerank_score"], 0.91)
        self.assertIn("texto", ctx)

    def test_empty_result_is_no_context(self):
        agent, stack = self._patched([])
        with stack[0], stack[1], stack[2]:
            ctx, sources, stats = agent.retrieve_context("q")
        self.assertEqual(sources, [])
        self.assertEqual(stats["mode"], "no_context")

    def test_degraded_native_does_not_refuse_by_score(self):
        import agent
        stats = {"native": True, "rerank_degraded": True}
        self.assertFalse(agent.insufficient_evidence([{"rerank_score": 0.01}], stats))


class TestIngestAuto(unittest.TestCase):
    def test_insert_chunks_auto_omits_embedding_and_keeps_metadata(self):
        import ingest
        from langchain_core.documents import Document
        inserted = []

        class Col:
            def insert_many(self, docs):
                inserted.extend(docs)

        chunks = [Document(page_content="t1", metadata={"page": 3, "file": "a.pdf"}),
                  Document(page_content="t2", metadata={})]
        n = ingest.insert_chunks_auto(Col(), chunks, "doc", "publico", None, batch_size=1)
        self.assertEqual(n, 2)
        self.assertTrue(all("embedding" not in d for d in inserted))
        self.assertEqual(inserted[0]["metadata"]["page"], 3)
        self.assertEqual(inserted[1]["metadata"]["chunk_id"], 1)
        self.assertEqual(inserted[0]["metadata"]["client_id"], CLIENT_ID)
        self.assertNotIn("expires_at", inserted[0]["metadata"])

    def test_mode_follows_flag_and_override(self):
        import ingest
        from unittest import mock
        with mock.patch.dict(os.environ, {"EMBED_MODE": "auto"}):
            self.assertTrue(ingest.auto_embed_mode())
        with mock.patch.dict(os.environ, {"EMBED_MODE": "manual"}):
            self.assertFalse(ingest.auto_embed_mode())


if __name__ == "__main__":
    unittest.main()
