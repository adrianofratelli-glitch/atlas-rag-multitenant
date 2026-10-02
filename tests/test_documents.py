"""Unit tests for the document-library helpers (no network, no Atlas)."""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("MONGO_URI", "mongodb://localhost/test")
os.environ.setdefault("VOYAGE_API_KEY", "test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("CLIENT_ID", "test-tenant")
os.environ["RAG_NATIVE"] = "0"  # estes testes cobrem o caminho clássico; não herdam a flag do .env

from backend.documents import (
    MAX_SOURCE_LENGTH,
    ProtectedDocumentError,
    UploadError,
    safe_source_name,
    validate_extension,
    validate_size,
)


class TestSafeSourceName(unittest.TestCase):
    def test_strips_accents_and_spaces(self):
        self.assertEqual(safe_source_name("Plano de Ação 2026.pdf"), "Plano-de-Acao-2026")

    def test_rejects_path_traversal(self):
        # Path().stem drops directories; the slug keeps no separators either.
        self.assertNotIn("/", safe_source_name("../../etc/passwd.txt"))

    def test_collapses_repeated_separators(self):
        self.assertEqual(safe_source_name("a   b---c.pdf"), "a-b-c")

    def test_truncates_long_names(self):
        name = safe_source_name("x" * 300 + ".pdf")
        self.assertLessEqual(len(name), MAX_SOURCE_LENGTH)

    def test_rejects_empty_result(self):
        with self.assertRaises(UploadError):
            safe_source_name("")


class TestValidateExtension(unittest.TestCase):
    def test_accepts_supported_format(self):
        self.assertEqual(validate_extension("relatorio.PDF"), ".pdf")

    def test_rejects_unsupported_format(self):
        with self.assertRaises(UploadError):
            validate_extension("payload.exe")

    def test_rejects_missing_extension(self):
        with self.assertRaises(UploadError):
            validate_extension("relatorio")


class TestValidateSize(unittest.TestCase):
    def test_rejects_empty_file(self):
        with self.assertRaises(UploadError):
            validate_size(0)

    def test_rejects_oversized_file(self):
        with self.assertRaises(UploadError):
            validate_size(10**9)

    def test_accepts_small_file(self):
        validate_size(1024)


class TestUploadTtl(unittest.TestCase):
    """The TTL is what keeps repeated demos from piling vectors up forever."""

    def test_default_ttl_is_24h(self):
        from backend.documents import UPLOAD_TTL_HOURS

        self.assertEqual(UPLOAD_TTL_HOURS, 24.0)

    def test_ingest_stamps_expires_at_only_when_ttl_given(self):
        import inspect

        import ingest as ingest_module

        signature = inspect.signature(ingest_module.ingest)
        self.assertIn("ttl_hours", signature.parameters)
        self.assertIsNone(signature.parameters["ttl_hours"].default)

class TestProtectedCorpus(unittest.TestCase):
    """The reference corpus (no TTL stamp) must never be deletable from the app."""

    def test_delete_refuses_protected_source(self):
        import backend.documents as docs

        original = docs.is_protected
        docs.is_protected = lambda source: True
        try:
            with self.assertRaises(ProtectedDocumentError):
                docs.delete_document("PDTIC_2025_2027")
        finally:
            docs.is_protected = original

    def test_delete_only_targets_ttl_stamped_chunks(self):
        import inspect

        import backend.documents as docs

        source = inspect.getsource(docs.delete_document)
        self.assertIn("metadata.expires_at", source)




class IsoUtcTests(unittest.TestCase):
    def test_naive_datetime_is_marked_utc(self):
        from datetime import datetime
        from backend.documents import iso_utc
        self.assertTrue(iso_utc(datetime(2026, 10, 3, 13, 41, 14)).endswith("+00:00"))

    def test_aware_datetime_is_converted_to_utc(self):
        from datetime import datetime, timedelta, timezone
        from backend.documents import iso_utc
        local = datetime(2026, 10, 3, 10, 41, 14, tzinfo=timezone(timedelta(hours=-3)))
        self.assertEqual(iso_utc(local), "2026-10-03T13:41:14+00:00")


if __name__ == "__main__":
    unittest.main()
