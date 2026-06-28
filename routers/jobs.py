from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from google.auth.exceptions import RefreshError
from auth import get_current_user, get_supabase
from credentials import hydrate_job_settings, mark_gsc_reconnect_required, strip_secret_fields
from abuse_protection import enforce_job_start, enforce_rate_limit, execute_active_job_write

router = APIRouter()

_GSC_RECONNECT_ERROR = "Google Search Console reconnect required."
_GSC_UNAVAILABLE_ERROR = "Selected Google Search Console connection unavailable."
_GSC_CONFIG_ERROR = "Google Search Console OAuth configuration missing."
_CREDENTIALS_UNAVAILABLE_ERROR = "Saved credentials are temporarily unavailable."
_RERUN_RESULTS_ERROR = "Re-run results could not be saved. Please try again."


from pydantic import BaseModel

class RenameRequest(BaseModel):
    name: str


@router.patch("/{job_id}/rename")
def rename_job(job_id: str, body: RenameRequest, user=Depends(get_current_user)):
    sb = get_supabase()
    res = (
        sb.table("jobs")
        .update({"name": body.name.strip()})
        .eq("id", job_id)
        .eq("user_id", user.id)
        .execute()
    )
    if not res.data:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"renamed": True}


@router.get("")
def list_jobs(user=Depends(get_current_user)):
    """Return all jobs for the current user, newest first."""
    sb = get_supabase()
    res = (
        sb.table("jobs")
        .select("id, name, status, total_rows, completed_rows, failed_rows, created_at, updated_at, error")
        .eq("user_id", user.id)
        .eq("tool", "meta")
        .order("created_at", desc=True)
        .limit(50)
        .execute()
    )
    return res.data or []


@router.get("/{job_id}")
def get_job(job_id: str, user=Depends(get_current_user)):
    """Return full job including results."""
    sb = get_supabase()
    res = (
        sb.table("jobs")
        .select("*")
        .eq("id", job_id)
        .eq("user_id", user.id)
        .execute()
    )
    if not res.data:
        raise HTTPException(status_code=404, detail="Job not found")
    job = res.data[0]
    return {**job, "settings": strip_secret_fields(job.get("settings"))}


@router.delete("/{job_id}")
def delete_job(job_id: str, user=Depends(get_current_user)):
    """Delete a job from history."""
    sb = get_supabase()
    res = (
        sb.table("jobs")
        .delete()
        .eq("id", job_id)
        .eq("user_id", user.id)
        .execute()
    )
    if not res.data:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"deleted": True}


class RerunRequest(BaseModel):
    keyword_override: str = ""


class MultiRerunRequest(BaseModel):
    row_indices: list[int]


def _persist_gsc_error(sb, job_id: str, user_id: str, message: str):
    try:
        sb.table("jobs").update({"error": message}).eq("id", job_id).eq("user_id", user_id).execute()
    except Exception:
        pass


def _clear_gsc_runtime_error(sb, job_id: str, user_id: str):
    try:
        (
            sb.table("jobs")
            .update({"error": None})
            .eq("id", job_id)
            .eq("user_id", user_id)
            .in_("error", [_GSC_UNAVAILABLE_ERROR, _GSC_RECONNECT_ERROR, _GSC_CONFIG_ERROR])
            .execute()
        )
    except Exception:
        pass


def _clear_credentials_runtime_error(sb, job_id: str, user_id: str):
    try:
        (
            sb.table("jobs")
            .update({"error": None})
            .eq("id", job_id)
            .eq("user_id", user_id)
            .in_("error", [_CREDENTIALS_UNAVAILABLE_ERROR])
            .execute()
        )
    except Exception:
        pass


def _persist_hydration_failure(sb, job_id: str, user_id: str, data: dict):
    try:
        sb.table("jobs").update(data).eq("id", job_id).eq("user_id", user_id).execute()
    except Exception:
        pass


def _persist_rerun_results_failure(sb, job_id: str, user_id: str):
    try:
        sb.table("jobs").update({
            "status": "failed",
            "error": _RERUN_RESULTS_ERROR,
            "current_step": "Re-run failed: results could not be saved.",
            "updated_at": "now()",
        }).eq("id", job_id).eq("user_id", user_id).execute()
    except Exception:
        pass


def _get_runtime_gsc_client(settings: dict, sb, user_id: str, job_id: str):
    if not settings.get("use_gsc"):
        return None
    credentials = settings.get("_gsc_credentials")
    if not credentials:
        _persist_gsc_error(sb, job_id, user_id, _GSC_UNAVAILABLE_ERROR)
        return None

    from utils.gsc import GscOAuthConfigError, get_gsc_client

    try:
        client = get_gsc_client(credentials)
        _clear_gsc_runtime_error(sb, job_id, user_id)
        return client
    except GscOAuthConfigError:
        _persist_gsc_error(sb, job_id, user_id, _GSC_CONFIG_ERROR)
    except RefreshError:
        if credentials.get("method") == "google_oauth":
            _persist_gsc_error(sb, job_id, user_id, _GSC_RECONNECT_ERROR)
            ciphertext = credentials.get("refresh_token_ciphertext")
            if ciphertext:
                try:
                    mark_gsc_reconnect_required(sb, user_id, ciphertext)
                except Exception:
                    pass
        else:
            _persist_gsc_error(sb, job_id, user_id, _GSC_UNAVAILABLE_ERROR)
    except Exception:
        _persist_gsc_error(sb, job_id, user_id, _GSC_UNAVAILABLE_ERROR)
    return None


@router.post("/{job_id}/rerun-row/{row_index}")
def rerun_row(
    job_id: str,
    row_index: int,
    body: RerunRequest = None,
    background_tasks: BackgroundTasks = None,
    user=Depends(get_current_user),
    sb=Depends(get_supabase),
):
    """Re-run a single row in a completed job, optionally with a keyword override."""
    res = sb.table("jobs").select("*").eq("id", job_id).eq("user_id", user.id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Job not found")

    job = res.data[0]
    rows = job.get("rows", [])
    settings = job.get("settings", {})

    if row_index < 0 or row_index >= len(rows):
        raise HTTPException(status_code=400, detail="Row index out of range")
    enforce_job_start(sb, user.id, "meta", 1, 150, exclude_job_id=job_id)
    enforce_rate_limit(sb, user.id, "meta", "row-rerun", 30)

    keyword_override = (body.keyword_override or "").strip() if body else ""

    step_msg = f"Re-running row {row_index + 1}"
    if keyword_override:
        step_msg += f' with keyword "{keyword_override}"'
    step_msg += "..."

    sb.table("jobs").update({
        "current_step": step_msg,
        "updated_at": "now()"
    }).eq("id", job_id).eq("user_id", user.id).execute()

    background_tasks.add_task(_rerun_single_row, job_id, row_index, rows, settings, sb, user.id, keyword_override)
    return {"status": "rerunning"}


def _rerun_single_row(job_id: str, row_index: int, rows: list, settings: dict, sb, user_id: str, keyword_override: str = ""):
    """Background task to re-run one row and update its result in place."""
    try:
        settings = hydrate_job_settings(sb, user_id, settings)
    except Exception:
        _persist_hydration_failure(sb, job_id, user_id, {
            "error": _CREDENTIALS_UNAVAILABLE_ERROR,
            "current_step": f"Row {row_index + 1} re-run failed: saved credentials are temporarily unavailable.",
            "updated_at": "now()",
        })
        return
    _clear_credentials_runtime_error(sb, job_id, user_id)
    import time
    from utils.dfs import get_keyword_overview, get_keyword_difficulty
    from utils.gsc import get_gsc_client, get_top_queries_for_url
    from utils.scraper import scrape_page_context

    try:
        row = rows[row_index]
        if keyword_override:
            row = {**row, "keyword": keyword_override}

        # api_key and dfs_password excluded from stored settings â€” fetch from user_settings
        api_key = settings.get("api_key", "")
        dfs_password = settings.get("dfs_password", "")
        if not api_key or not dfs_password:
            try:
                creds_res = sb.table("user_settings").select("provider_settings").eq("user_id", user_id).execute()
                if creds_res.data:
                    ps = creds_res.data[0].get("provider_settings") or {}
                    if not api_key:
                        api_key = ps.get("api_key", "")
                    if not dfs_password:
                        dfs_password = ps.get("dfs_password", "")
            except Exception:
                pass

        gsc_client = _get_runtime_gsc_client(settings, sb, user_id, job_id)

        branded_terms = [b.strip() for b in settings.get("brand_name", "").split() if b.strip()]
        full_brand_name = settings.get("full_brand_name", "").strip()
        if full_brand_name:
            import re as _re
            full_name_words = [w.lower() for w in _re.findall(r"[a-zA-Z]+", full_brand_name) if len(w) >= 3]
            branded_terms = list(set(branded_terms + full_name_words))
        branded_terms_input = settings.get("branded_terms_input", "").strip()
        if branded_terms_input:
            manual_terms = [t.strip().lower() for t in branded_terms_input.splitlines() if t.strip()]
            branded_terms = list(set(branded_terms + manual_terms))

        settings_with_key = {**settings, "api_key": api_key, "dfs_password": dfs_password}

        # Re-fetch brand profile if one was used on the original job
        brand_profile = {}
        brand_profile_id = settings.get("brand_profile_id")
        if brand_profile_id:
            try:
                bp_res = sb.table("brand_profiles").select("data").eq("id", brand_profile_id).eq("user_id", user_id).execute()
                if bp_res.data:
                    brand_profile = bp_res.data[0].get("data") or {}
            except Exception:
                pass

        from routers.meta import _process_single_row, _safe_gsc_auth_method, _update_job
        gsc_auth_method = _safe_gsc_auth_method(settings, settings.get("_gsc_credentials"), gsc_client)
        result = _process_single_row(
            row=row,
            settings=settings_with_key,
            gsc_client=gsc_client,
            branded_terms=branded_terms,
            used_keywords=set(),
            sb=sb,
            job_id=job_id,
            user_id=user_id,
            row_num=row_index + 1,
            total_rows=len(rows),
            brand_profile=brand_profile,
            gsc_auth_method=gsc_auth_method,
        )

        # Update just this row's result in the existing results array
        res = sb.table("jobs").select("results").eq("id", job_id).eq("user_id", user_id).execute()
        current_results = res.data[0].get("results", []) if res.data else []

        # Extend if needed
        while len(current_results) <= row_index:
            current_results.append({})
        current_results[row_index] = result

        sb.table("jobs").update({
            "results": current_results,
            "current_step": f"Row {row_index + 1} complete.",
            "updated_at": "now()"
        }).eq("id", job_id).eq("user_id", user_id).execute()

    except Exception:
        try:
            sb.table("jobs").update({
                "current_step": f"Row {row_index + 1} failed: row processing failed.",
                "updated_at": "now()"
            }).eq("id", job_id).eq("user_id", user_id).execute()
        except Exception:
            pass


@router.post("/{job_id}/cancel")
def cancel_job(job_id: str, user=Depends(get_current_user), sb=Depends(get_supabase)):
    """Cancel a running job."""
    res = sb.table("jobs").select("id, status, user_id").eq("id", job_id).eq("user_id", user.id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Job not found")
    if res.data[0]["status"] != "running":
        raise HTTPException(status_code=400, detail=f"Job is not running (status: {res.data[0]['status']})")
    sb.table("jobs").update({
        "status": "cancelled",
        "current_step": "Cancelled — stopping after current row...",
    }).eq("id", job_id).eq("user_id", user.id).execute()
    return {"cancelled": True}


@router.post("/{job_id}/rerun-rows")
def rerun_rows(
    job_id: str,
    body: MultiRerunRequest,
    background_tasks: BackgroundTasks,
    user=Depends(get_current_user),
    sb=Depends(get_supabase),
):
    """Re-run multiple rows from a completed job."""
    res = sb.table("jobs").select("*").eq("id", job_id).eq("user_id", user.id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Job not found")
    job = res.data[0]
    rows = job.get("rows", [])
    settings = job.get("settings", {})
    valid_indices = [i for i in body.row_indices if 0 <= i < len(rows)]
    if not valid_indices:
        raise HTTPException(status_code=400, detail="No valid row indices provided")
    enforce_job_start(sb, user.id, "meta", len(valid_indices), 150, exclude_job_id=job_id)
    enforce_rate_limit(sb, user.id, "meta", "bulk-rerun", 10)
    execute_active_job_write(lambda: sb.table("jobs").update({
        "status": "running",
        "current_step": f"Re-running {len(valid_indices)} row(s)...",
        "updated_at": "now()",
    }).eq("id", job_id).eq("user_id", user.id).execute(), "meta")
    background_tasks.add_task(_rerun_multiple_rows, job_id, valid_indices, rows, settings, sb, user.id)
    return {"status": "rerunning", "row_count": len(valid_indices)}


def _rerun_multiple_rows(job_id: str, row_indices: list, rows: list, settings: dict, sb, user_id: str):
    """Run multiple rows sequentially, updating results in place."""
    try:
        settings = hydrate_job_settings(sb, user_id, settings)
    except Exception:
        _persist_hydration_failure(sb, job_id, user_id, {
            "status": "failed",
            "error": _CREDENTIALS_UNAVAILABLE_ERROR,
            "current_step": "Re-run failed: saved credentials are temporarily unavailable.",
            "updated_at": "now()",
        })
        return
    _clear_credentials_runtime_error(sb, job_id, user_id)
    import re as _re
    from routers.meta import _process_single_row, _safe_gsc_auth_method, _update_job
    from utils.gsc import get_gsc_client

    # Fetch credentials from user_settings
    api_key = settings.get("api_key", "")
    dfs_password = settings.get("dfs_password", "")
    if not api_key or not dfs_password:
        try:
            creds = sb.table("user_settings").select("provider_settings").eq("user_id", user_id).execute()
            if creds.data:
                ps = creds.data[0].get("provider_settings") or {}
                if not api_key:
                    api_key = ps.get("api_key", "")
                if not dfs_password:
                    dfs_password = ps.get("dfs_password", "")
        except Exception:
            pass

    gsc_client = _get_runtime_gsc_client(settings, sb, user_id, job_id)
    gsc_auth_method = _safe_gsc_auth_method(settings, settings.get("_gsc_credentials"), gsc_client)

    branded_terms = [b.strip() for b in settings.get("brand_name", "").split() if b.strip()]
    full_brand = settings.get("full_brand_name", "").strip()
    if full_brand:
        branded_terms = list(set(branded_terms + [w.lower() for w in _re.findall(r"[a-zA-Z]+", full_brand) if len(w) >= 3]))
    branded_input = settings.get("branded_terms_input", "").strip()
    if branded_input:
        branded_terms = list(set(branded_terms + [t.strip().lower() for t in branded_input.splitlines() if t.strip()]))

    brand_profile = {}
    brand_profile_id = settings.get("brand_profile_id")
    if brand_profile_id:
        try:
            bp_res = sb.table("brand_profiles").select("data").eq("id", brand_profile_id).eq("user_id", user_id).execute()
            if bp_res.data:
                brand_profile = bp_res.data[0].get("data") or {}
        except Exception:
            pass

    try:
        res = sb.table("jobs").select("results").eq("id", job_id).eq("user_id", user_id).execute()
    except Exception:
        _persist_rerun_results_failure(sb, job_id, user_id)
        return
    results = list(res.data[0].get("results") or []) if res.data else []
    while len(results) < len(rows):
        results.append({})

    for n, row_index in enumerate(row_indices):
        _update_job(sb, job_id, user_id, {
            "current_step": f"Re-running row {row_index + 1} ({n + 1}/{len(row_indices)})...",
        })
        try:
            result = _process_single_row(
                row=rows[row_index],
                settings={**settings, "api_key": api_key, "dfs_password": dfs_password},
                gsc_client=gsc_client,
                branded_terms=branded_terms,
                used_keywords=set(),
                sb=sb,
                job_id=job_id,
                user_id=user_id,
                row_num=row_index + 1,
                total_rows=len(rows),
                brand_profile=brand_profile,
                gsc_auth_method=gsc_auth_method,
            )
            results[row_index] = result
        except Exception:
            results[row_index] = {
                "url": rows[row_index].get("url", ""),
                "error": "Row processing failed.",
                "status": "error",
                "gsc_auth_method": gsc_auth_method,
            }

    try:
        sb.table("jobs").update({
        "status": "complete",
        "current_step": f"Re-run complete — {len(row_indices)} row(s) updated.",
        "results": results,
        "failed_rows": sum(1 for r in results if r.get("error") or r.get("status") == "error"),
        "updated_at": "now()",
        }).eq("id", job_id).eq("user_id", user_id).execute()
    except Exception:
        _persist_rerun_results_failure(sb, job_id, user_id)


@router.post("/{job_id}/duplicate")
def duplicate_job(
    job_id: str,
    user=Depends(get_current_user),
    sb=Depends(get_supabase),
):
    """Duplicate a job's settings and rows as a new draft job."""
    res = sb.table("jobs").select("*").eq("id", job_id).eq("user_id", user.id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Job not found")

    original = res.data[0]
    enforce_rate_limit(sb, user.id, "meta", "job-create", 10)

    new_job = {
        "user_id": user.id,
        "status": "draft",
        "name": f"{original.get('name', 'Job')} (copy)",
        "settings": strip_secret_fields(original.get("settings")),
        "rows": original.get("rows", []),
        "results": [],
        "total_rows": original.get("total_rows", 0),
        "completed_rows": 0,
        "current_step": "",
    }

    new_res = sb.table("jobs").insert(new_job).execute()
    if not new_res.data:
        raise HTTPException(status_code=500, detail="Failed to duplicate job")

    return {"job_id": new_res.data[0]["id"]}
