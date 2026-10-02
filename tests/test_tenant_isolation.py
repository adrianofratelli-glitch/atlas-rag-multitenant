"""Isolamento de tenant provado end-to-end no retrieve_context (sem rede).

A coleção fake AVALIA os filtros das pipelines de verdade ($vectorSearch.filter e
$search.compound.filter) contra um corpus que contém chunks de OUTRO tenant. Se alguém
remover o filtro metadata.client_id de qualquer pipeline, estes testes falham.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("MONGO_URI", "mongodb://localhost/test")
os.environ.setdefault("VOYAGE_API_KEY", "test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("CLIENT_ID", "test-tenant")
os.environ["RAG_NATIVE"] = "0"  # estes testes cobrem o caminho clássico; não herdam a flag do .env

import agent
from agent import CLIENT_ID, retrieve_context

FOREIGN = "outro-tenant"


def _get(doc, path):
    for part in path.split("."):
        doc = doc.get(part) if isinstance(doc, dict) else None
    return doc


def _match(doc, flt):
    if "$and" in flt:
        return all(_match(doc, f) for f in flt["$and"])
    for path, cond in flt.items():
        val = _get(doc, path)
        if isinstance(cond, dict) and "$in" in cond:
            if val not in cond["$in"]:
                return False
        elif val != cond:
            return False
    return True


def _match_search(doc, flt_list):
    return all(_get(doc, f["in"]["path"]) in f["in"]["value"] for f in flt_list)


class FakeCollection:
    """Devolve, por ordem de 'score', só os docs que satisfazem o filtro da pipeline."""

    def __init__(self, docs):
        self.docs = docs

    def aggregate(self, pipeline):
        stage = pipeline[0]
        if "$vectorSearch" in stage:
            vs = stage["$vectorSearch"]
            rows = [d for d in self.docs if _match(d, vs["filter"])]
            rows = rows[: vs["limit"]]
            return [{**d, "vector_score": d["score"]} for d in rows]
        s = stage["$search"]["compound"]
        rows = [d for d in self.docs if _match_search(d, s["filter"])]
        rows = rows[: pipeline[1]["$limit"]]
        return [{**d, "search_score": d["score"]} for d in rows]


def _corpus():
    docs = []
    # Os chunks do outro tenant têm score MAIOR: sem filtro, dominariam qualquer k.
    for i in range(30):
        docs.append({"_id": f"foreign-{i}", "text": f"segredo do outro tenant {i}", "score": 1.0 - i * 0.001,
                     "metadata": {"client_id": FOREIGN, "nivel_acesso": "publico", "source": "x.pdf", "page": i}})
    for i in range(30):
        docs.append({"_id": f"own-{i}", "text": f"conteúdo legítimo {i}", "score": 0.5 - i * 0.001,
                     "metadata": {"client_id": CLIENT_ID, "nivel_acesso": "publico", "source": "corpus.pdf", "page": i}})
    return docs


class FakeRerank:
    def __init__(self, docs):
        self.results = [mock.Mock(index=i, relevance_score=1.0 - i * 0.01) for i in range(len(docs))]


class TenantIsolationTests(unittest.TestCase):
    def _run(self, top_k, capture, **kw):
        voyage = mock.Mock()
        voyage.rerank.side_effect = lambda q, docs, **k: FakeRerank(docs[: k["top_k"]])
        with mock.patch.object(agent, "_get_voyage", return_value=voyage), \
             mock.patch.object(agent, "_embed_query", return_value=[0.1] * 4), \
             mock.patch.object(agent, "get_client", return_value={agent.DB_NAME: {"documents": FakeCollection(_corpus())}}):
            return retrieve_context("qualquer pergunta", top_k=top_k, _capture=capture, **kw)

    def test_foreign_tenant_never_returned_for_any_k(self):
        for k in (1, 3, 5, 8, 15, 30, 60):
            for kw in ({}, {"use_lexical": False}, {"use_rerank": False}, {"final_n": 20}):
                with self.subTest(k=k, **kw):
                    captured = []
                    context, sources, _ = self._run(k, captured, **kw)
                    self.assertTrue(captured, "retrieval devolveu vazio: o teste não provaria nada")
                    self.assertTrue(all(c["metadata"]["client_id"] == CLIENT_ID for c in captured))
                    self.assertFalse(any(str(c["chunk_id"]).startswith("foreign") for c in captured))
                    self.assertNotIn("outro tenant", context)
                    self.assertNotIn("x.pdf", [s["source"] for s in sources])

    def test_test_is_sensitive_to_missing_filter(self):
        """Mutação: sem o filtro de client_id nas pipelines, o chunk estrangeiro VAZA — prova que o teste detecta a regressão."""
        real_v, real_l = agent._vector_pipeline, agent._lexical_pipeline

        def no_tenant_v(*a, **k):
            p = real_v(*a, **k)
            p[0]["$vectorSearch"]["filter"] = {"metadata.nivel_acesso": {"$in": ["publico"]}}
            return p

        def no_tenant_l(*a, **k):
            p = real_l(*a, **k)
            p[0]["$search"]["compound"]["filter"] = p[0]["$search"]["compound"]["filter"][1:]
            return p

        captured = []
        with mock.patch.object(agent, "_vector_pipeline", no_tenant_v), mock.patch.object(agent, "_lexical_pipeline", no_tenant_l):
            self._run(15, captured)
        self.assertTrue(any(str(c["chunk_id"]).startswith("foreign") for c in captured))


if __name__ == "__main__":
    unittest.main()
