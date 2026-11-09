"""O /api/health não pode pagar um ping ao Atlas em toda chamada (o Portal sonda com ~0,8 s)."""
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend import api


class HealthCache(unittest.TestCase):
    def setUp(self):
        api._status_cache.update(ts=0, online=False, chunks=None)

    def test_chamadas_seguidas_reaproveitam_o_ping(self):
        with patch.object(api, "_ping_atlas", return_value=(True, 7)) as ping:
            c = TestClient(api.app)
            for _ in range(5):
                r = c.get("/api/health")
                self.assertEqual(r.status_code, 200)
                self.assertEqual(r.json()["chunks"], 7)
            self.assertEqual(ping.call_count, 1)

    def test_queda_do_mongo_vira_503_depois_do_cache(self):
        with patch.object(api, "_ping_atlas", return_value=(False, None)):
            r = TestClient(api.app).get("/api/health")
            self.assertEqual(r.status_code, 503)
            self.assertEqual(r.json()["status"], "degraded")


if __name__ == "__main__":
    unittest.main()
