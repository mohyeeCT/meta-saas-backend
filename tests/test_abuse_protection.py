import unittest

from fastapi import HTTPException

from abuse_protection import enforce_job_start, execute_active_job_write


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
