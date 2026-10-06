"""One contract, run against every Store implementation. MemoryStore always; PostgresStore when AGENT_TEST_DATABASE_URL is set
(CI provides a Postgres service; locally any disposable database works). Skipped, not faked, when no database is available."""
import os, threading, unittest

try:
    from service.store import MemoryStore
except ImportError:                      # pragma: no cover
    MemoryStore = None

PG_URL = os.environ.get("AGENT_TEST_DATABASE_URL", "")


class StoreContract:
    def make(self):
        raise NotImplementedError

    def setUp(self):
        self.s = self.make()
        self.s.init()

    def tearDown(self):
        self.s.close()

    def test_run_lifecycle(self):
        r = self.s.create_run("compute 6 x 7", 5, request_id="rq1")
        self.assertEqual((r["status"], r["max_steps"], r["request_id"]), ("queued", 5, "rq1"))
        self.s.update_run(r["id"], status="completed", final_answer="42", tokens=99, evaluation={"grounded": True}, state_json='{"a": 1}', finished_at="2026-01-01T00:00:00+00:00")
        g = self.s.get_run(r["id"])
        self.assertEqual((g["status"], g["final_answer"], g["tokens"], g["evaluation"]), ("completed", "42", 99, {"grounded": True}))
        self.assertIsNone(self.s.get_run("nope"))
        with self.assertRaises(Exception):
            self.s.update_run(r["id"], objective="hack")

    def test_list_runs_newest_first(self):
        ids = [self.s.create_run(f"o{i}", 3)["id"] for i in range(3)]
        self.assertEqual([r["id"] for r in self.s.list_runs(10)], ids[::-1])
        self.assertEqual(len(self.s.list_runs(2)), 2)
        self.assertEqual(len(self.s.list_runs(10, offset=2)), 1)

    def test_events_are_ordered_gapless_and_resumable(self):
        rid = self.s.create_run("o", 3)["id"]
        seqs = [self.s.append_event(rid, "t", {"i": i}) for i in range(5)]
        self.assertEqual(seqs, [1, 2, 3, 4, 5])
        self.assertEqual([e["seq"] for e in self.s.events(rid, after=3)], [4, 5])
        self.assertEqual(self.s.events(rid, after=5), [])
        self.assertEqual(self.s.events(rid)[0]["payload"], {"i": 0})

    def test_concurrent_appends_never_duplicate_seq(self):
        rid = self.s.create_run("o", 3)["id"]
        def work():
            for _ in range(20):
                self.s.append_event(rid, "t", {})
        ts = [threading.Thread(target=work) for _ in range(5)]
        [t.start() for t in ts]; [t.join() for t in ts]
        self.assertEqual([e["seq"] for e in self.s.events(rid, limit=1000)], list(range(1, 101)))

    def test_approval_decided_exactly_once(self):
        rid = self.s.create_run("o", 3)["id"]
        aid = self.s.create_approval(rid, "write_file", {"path": "a"})
        self.assertEqual(self.s.get_approval(aid)["status"], "pending")
        self.assertTrue(self.s.decide_approval(aid, True))
        self.assertFalse(self.s.decide_approval(aid, False), "second decision must not flip the first")
        self.assertEqual(self.s.get_approval(aid)["status"], "approved")
        self.assertEqual(len(self.s.list_approvals(rid)), 1)
        self.assertFalse(self.s.decide_approval("missing", True))

    def test_evals_and_interrupted(self):
        self.s.save_eval({"completion_rate": 1.0}, [{"case": "a"}])
        self.assertEqual(self.s.list_evals(5)[0]["aggregate"], {"completion_rate": 1.0})
        a = self.s.create_run("o", 3)["id"]; b = self.s.create_run("o", 3)["id"]
        self.s.update_run(a, status="running"); self.s.update_run(b, status="completed")
        self.assertEqual(self.s.mark_interrupted(), 1)
        self.assertEqual((self.s.get_run(a)["status"], self.s.get_run(a)["stop_reason"]), ("failed", "interrupted"))
        self.assertEqual(self.s.get_run(b)["status"], "completed")


@unittest.skipIf(MemoryStore is None, "service dependencies not installed")
class MemoryStoreTests(StoreContract, unittest.TestCase):
    def make(self):
        return MemoryStore()


@unittest.skipUnless(PG_URL, "AGENT_TEST_DATABASE_URL not set")
class PostgresStoreTests(StoreContract, unittest.TestCase):
    def make(self):
        from service.pg_store import PostgresStore
        import psycopg
        with psycopg.connect(PG_URL, autocommit=True) as c:       # fresh schema per test: drop everything the migrator creates
            c.execute("DROP TABLE IF EXISTS approvals, run_events, eval_runs, runs, schema_migrations CASCADE")
        return PostgresStore(PG_URL)

    def test_migrations_idempotent_and_recorded(self):
        from service.migrate import migrate, migration_files
        import psycopg
        with psycopg.connect(PG_URL, autocommit=True) as c:
            self.assertEqual(migrate(c), [])
            self.assertEqual([r[0] for r in c.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()], migration_files())


if __name__ == "__main__":
    unittest.main()
