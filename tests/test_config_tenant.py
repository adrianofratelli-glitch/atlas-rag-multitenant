"""CLIENT_ID must be mandatory: an operator forgetting to set it (or copying a
.env from another tenant) must not silently boot against `rag_default`.

Run in a subprocess with a fresh interpreter: config.py raises at import
time, and by the time this test module runs, other test modules in the same
process may have already imported `config` successfully (with CLIENT_ID set)
— Python caches that import, so re-importing in-process would not re-run the
top-level check.
"""
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run_import_config(extra_env: dict) -> subprocess.CompletedProcess:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "MONGO_URI": "mongodb://localhost/test",
        "VOYAGE_API_KEY": "test",
        "ANTHROPIC_API_KEY": "test",
        "PYTHONPATH": ROOT,
    }
    env.update(extra_env)
    # Run from an empty scratch directory, not ROOT: the repo's real .env sets
    # CLIENT_ID (config.py calls load_dotenv()), which would mask exactly the
    # "operator forgot to set CLIENT_ID" scenario this test exercises.
    with tempfile.TemporaryDirectory() as scratch:
        return subprocess.run(
            [sys.executable, "-c", "import config"],
            cwd=scratch,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )


class TestClientIdMandatory(unittest.TestCase):
    def test_boot_fails_without_client_id(self):
        result = _run_import_config({})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("CLIENT_ID", result.stderr)

    def test_boot_succeeds_with_client_id(self):
        result = _run_import_config({"CLIENT_ID": "acme"})
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_boot_succeeds_with_explicit_default_opt_in(self):
        result = _run_import_config({"ALLOW_DEFAULT_TENANT": "true"})
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_boot_fails_without_client_id_even_if_opt_in_is_falsy(self):
        result = _run_import_config({"ALLOW_DEFAULT_TENANT": "false"})
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
