"""O slot de concorrência do /api/chat não pode vazar quando o cliente desconecta no meio do stream."""
import os
import sys
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("MONGO_URI", "mongodb://localhost/test")
os.environ.setdefault("VOYAGE_API_KEY", "test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("CLIENT_ID", "test-tenant")
os.environ["RAG_NATIVE"] = "0"

from backend.api import SlotLease


class SlotLeaseTests(unittest.TestCase):
    def test_release_is_idempotent(self):
        sem = threading.BoundedSemaphore(2)
        sem.acquire()
        lease = SlotLease(sem)
        lease.release()
        lease.release()  # segunda liberação não pode estourar o BoundedSemaphore nem inflar a capacidade
        self.assertTrue(sem.acquire(blocking=False))
        self.assertTrue(sem.acquire(blocking=False))
        self.assertFalse(sem.acquire(blocking=False))

    def test_release_from_two_threads_frees_one_slot(self):
        sem = threading.BoundedSemaphore(1)
        sem.acquire()
        lease = SlotLease(sem)
        threads = [threading.Thread(target=lease.release) for _ in range(8)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertTrue(sem.acquire(blocking=False))
        self.assertFalse(sem.acquire(blocking=False))


if __name__ == "__main__":
    unittest.main()
