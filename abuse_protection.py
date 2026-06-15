import logging
import math

from fastapi import HTTPException


ACTIVE_JOB_STATUSES = ("pending", "running", "cancelling")
RATE_LIMIT_WINDOW_SECONDS = 600
RATE_LIMIT_LABELS = {"job-create": "job creation", "row-rerun": "row rerun", "bulk-rerun": "bulk rerun", "section-rerun": "section rerun"}


def enforce_rate_limit(sb, user_id: str, tool: str, action: str, limit: int):
    try:
        result = sb.rpc("check_rate_limit", {"p_user_id": user_id, "p_tool": tool, "p_action": action, "p_limit": limit, "p_window_seconds": RATE_LIMIT_WINDOW_SECONDS}).execute()
    except Exception:
        logging.exception("Rate-limit check failed open for %s/%s", tool, action)
        return
    row = result.data[0] if isinstance(result.data, list) and result.data else result.data
    if not isinstance(row, dict) or row.get("allowed", True):
        return
    retry_after = max(1, math.ceil(float(row.get("retry_after_seconds") or 1)))
    wait = f"{retry_after} seconds" if retry_after < 60 else f"{math.ceil(retry_after / 60)} minutes"
    raise HTTPException(status_code=429, detail=f"Too many {RATE_LIMIT_LABELS.get(action, 'request')} requests. Please wait {wait} before trying again.", headers={"Retry-After": str(retry_after)})


def execute_active_job_write(operation, tool: str):
    try:
        return operation()
    except Exception as exc:
        if "jobs_one_active_per_user_tool_idx" in str(exc):
            raise HTTPException(status_code=409, detail=f"A {tool} job is already active. Wait for it to finish or cancel it before starting another.") from exc
        raise


def enforce_job_start(sb, user_id: str, tool: str, row_count: int, max_rows: int, exclude_job_id: str | None = None):
    if row_count < 1:
        raise HTTPException(status_code=400, detail="Add at least one URL before starting a job.")
    if row_count > max_rows:
        raise HTTPException(status_code=400, detail=f"This workflow's maximum is {max_rows} URLs per job.")
    try:
        query = sb.table("jobs").select("id").eq("user_id", user_id).eq("tool", tool).in_("status", ACTIVE_JOB_STATUSES)
        if exclude_job_id:
            query = query.neq("id", exclude_job_id)
        result = query.limit(1).execute()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Unable to verify active jobs. Please try again.") from exc
    if result.data:
        raise HTTPException(status_code=409, detail=f"A {tool} job is already active. Wait for it to finish or cancel it before starting another.")
