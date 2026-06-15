import unittest

from fastapi import HTTPException

from abuse_protection import enforce_job_start, enforce_rate_limit, execute_active_job_write


class FakeQuery:
    def __init__(self, rows): self.rows = rows
    def select(self, *_args): return self
    def eq(self, field, value):
        self.rows = [row for row in self.rows if row.get(field) == value]; return self
    def in_(self, field, values):
        self.rows = [row for row in self.rows if row.get(field) in values]; return self
    def neq(self, field, value):
        self.rows = [row for row in self.rows if row.get(field) != value]; return self
    def limit(self, count): self.rows = self.rows[:count]; return self
    def execute(self): return type("Result", (), {"data": self.rows})()


class FakeSupabase:
    def __init__(self, rows=None): self.rows = rows or []
    def table(self, name): return FakeQuery(list(self.rows))


class AbuseProtectionTests(unittest.TestCase):
    def test_limits_and_concurrency(self):
        enforce_job_start(FakeSupabase(), "user-1", "meta", 150, 150)
        with self.assertRaisesRegex(HTTPException, "maximum is 150"):
            enforce_job_start(FakeSupabase(), "user-1", "meta", 151, 150)
        active = FakeSupabase([{"id": "job-1", "user_id": "user-1", "tool": "meta", "status": "cancelling"}])
        with self.assertRaisesRegex(HTTPException, "already active"):
            enforce_job_start(active, "user-1", "meta", 1, 150)
        enforce_job_start(active, "user-1", "meta", 1, 150, exclude_job_id="job-1")
        with self.assertRaisesRegex(HTTPException, "already active"):
            execute_active_job_write(lambda: (_ for _ in ()).throw(RuntimeError("jobs_one_active_per_user_tool_idx")), "meta")

    def test_rate_limit_contract(self):
        class Rpc:
            def __init__(self, data=None, error=None): self.data, self.error, self.params = data, error, None
            def rpc(self, name, params): self.params = params; return self
            def execute(self):
                if self.error: raise self.error
                return type("Result", (), {"data": self.data})()
        allowed = Rpc([{"allowed": True, "retry_after_seconds": 0}])
        enforce_rate_limit(allowed, "user-1", "meta", "job-create", 10)
        self.assertEqual(allowed.params["p_window_seconds"], 600)
        with self.assertRaises(HTTPException) as raised:
            enforce_rate_limit(Rpc([{"allowed": False, "retry_after_seconds": 181}]), "user-1", "meta", "bulk-rerun", 10)
        self.assertEqual(raised.exception.headers["Retry-After"], "181")
        self.assertIn("Please wait 4 minutes", raised.exception.detail)
        enforce_rate_limit(Rpc(error=RuntimeError("unavailable")), "user-1", "meta", "job-create", 10)
