import unittest
from unittest.mock import MagicMock, patch
from pymongo.errors import DuplicateKeyError
import db

class TenantIdentityRaceTests(unittest.TestCase):
    def verify_race(self, winner):
        collection = MagicMock()
        collection.find_one.side_effect = [None, {"client_id": winner}]
        collection.insert_one.side_effect = DuplicateKeyError("concurrent startup")
        client = MagicMock()
        client.__getitem__.return_value.__getitem__.return_value = collection
        with patch.object(db, "get_client", return_value=client):
            db.verify_tenant_identity("test_tenant", "expected")

    def test_same_tenant_concurrent_start_succeeds(self):
        self.verify_race("expected")

    def test_other_tenant_concurrent_start_fails_closed(self):
        with self.assertRaises(db.TenantIdentityMismatch):
            self.verify_race("different")
