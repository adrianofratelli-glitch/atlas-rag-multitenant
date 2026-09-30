"""Unit tests for backend/api.py's pure helper functions (no network)."""

import os
import sys
import unittest

from pydantic import ValidationError

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("MONGO_URI", "mongodb://localhost/test")
os.environ.setdefault("VOYAGE_API_KEY", "test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("CLIENT_ID", "test-tenant")

from backend.api import (
    ChatBody,
    _clean,
    _get_document_outline,
    _is_obviously_out_of_scope,
    _levels_for,
    _scope_reply,
)
import backend.documents as documents_module


class TestClean(unittest.TestCase):
    def test_strips_leading_emoji(self):
        self.assertEqual(_clean("📋 O que este documento aborda?"), "O que este documento aborda?")

    def test_no_emoji_unchanged(self):
        self.assertEqual(_clean("Quais são os prazos?"), "Quais são os prazos?")

    def test_strips_surrounding_whitespace(self):
        self.assertEqual(_clean("🎯  Objetivo  "), "Objetivo")


class TestLevelsFor(unittest.TestCase):
    def test_publico_sees_only_public(self):
        self.assertEqual(_levels_for("publico"), ["publico"])

    def test_restrito_sees_both(self):
        self.assertEqual(_levels_for("restrito"), ["publico", "restrito"])

    def test_unknown_value_defaults_to_public_only(self):
        """Default-deny: any value other than the exact "restrito" falls back to
        public-only access."""
        self.assertEqual(_levels_for("qualquer-coisa"), ["publico"])
        self.assertEqual(_levels_for(""), ["publico"])

    def test_chat_body_defaults_to_public_only(self):
        self.assertEqual(ChatBody(question="teste").access_level, "publico")

    def test_chat_body_rejects_oversized_total_history(self):
        with self.assertRaises(ValidationError):
            ChatBody(
                question="teste",
                messages=[{"role": "user", "content": "x" * 12_000}] * 5,
            )


class TestScopeRecovery(unittest.TestCase):
    def test_temperature_is_redirected_without_rag(self):
        self.assertTrue(_is_obviously_out_of_scope("Qual é a temperatura hoje?"))
        self.assertIn("Posso ajudar", _scope_reply())

    def test_document_question_stays_in_scope(self):
        self.assertFalse(_is_obviously_out_of_scope("Quais são os objetivos estratégicos?"))


class _FakeAggregateCollection:
    """Records the pipeline it was called with; returns no rows."""

    def __init__(self):
        self.calls = []

    def aggregate(self, pipeline):
        self.calls.append(pipeline)
        return iter([])


class _FakeDB(dict):
    def __getitem__(self, name):
        return self.setdefault(name, _FakeAggregateCollection())


class TestDocumentOutlineACL(unittest.TestCase):
    """The outline (injected into the system prompt) must respect the caller's
    ACL — same access-control filter used by retrieval, not the whole
    collection. A `publico` caller must never see a preview of `restrito`
    pages, even indirectly through the outline cache."""

    def setUp(self):
        import backend.api as api_module

        self.api_module = api_module
        self.fake_db = _FakeDB()
        self._orig_get_client = api_module.get_client
        api_module.get_client = lambda: {api_module.DB_NAME: self.fake_db}
        self._orig_cache = dict(api_module._outline_cache)
        api_module._outline_cache.clear()
        # Force a fresh corpus_version so the cache key doesn't collide with
        # anything another test/run might have left behind.
        documents_module.corpus_version += 1

    def tearDown(self):
        self.api_module.get_client = self._orig_get_client
        self.api_module._outline_cache.clear()
        self.api_module._outline_cache.update(self._orig_cache)

    def test_outline_match_stage_filters_by_access_level(self):
        _get_document_outline(["publico"])
        col = self.fake_db["documents"]
        pipeline = col.calls[-1]
        self.assertEqual(
            pipeline[0],
            {"$match": {"metadata.nivel_acesso": {"$in": ["publico"]}}},
        )

    def test_outline_cache_key_varies_by_access_level(self):
        """A publico-only outline must not be served (from cache) to a
        restrito caller, or vice versa — the two access levels must produce
        independent cache entries and independent Mongo calls."""
        col = self.fake_db["documents"]
        _get_document_outline(["publico"])
        calls_after_publico = len(col.calls)
        _get_document_outline(["publico", "restrito"])
        self.assertGreater(len(col.calls), calls_after_publico)


if __name__ == "__main__":
    unittest.main()
