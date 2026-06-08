import time
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, BackgroundTasks, HTTPException
from pydantic import BaseModel

from auth import get_current_user
from auth import get_supabase
from utils.gsc import get_gsc_client, get_top_queries_for_url
from utils.dfs import get_keyword_overview, get_keyword_difficulty
from utils.keyword import select_keyword
from utils.niches import get_niche_context
from utils.copy_gen import generate_copy

router = APIRouter()

_RATE_LIMITS = {
    "Claude": 0.5,
    "OpenAI": 0.5,
    "Gemini (free)": 5.0,
    "Mistral (free tier)": 2.0,
    "Groq (free tier)": 2.0,
}


def _is_cancelled(sb, job_id: str) -> bool:
    try:
        res = sb.table("jobs").select("status").eq("id", job_id).execute()
        return res.data and res.data[0].get("status") == "cancelling"
    except Exception:
        return False


def _update_job(sb, job_id: str, data: dict):
    try:
        update_data = {**data, "updated_at": "now()"}
        if "current_step" in data and data["current_step"]:
            log_entry = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "msg": data["current_step"],
            }
            try:
                res = sb.table("jobs").select("logs").eq("id", job_id).execute()
                current_logs = (res.data[0].get("logs") or []) if res.data else []
                current_logs.append(log_entry)
                update_data["logs"] = current_logs
            except Exception:
                pass
        sb.table("jobs").update(update_data).eq("id", job_id).execute()
    except Exception:
        pass


def _process_single_row(
    row: dict,
    settings: dict,
    gsc_client,
    branded_terms: list,
    used_keywords: set,
    sb,
    job_id: str,
    row_num: int,
    total_rows: int,
    brand_profile: dict = None,
) -> dict:
    def step(msg: str):
        _update_job(sb, job_id, {"current_step": f"Row {row_num}/{total_rows}: {msg}"})

    url          = (row.get("url") or "").strip()
    manual_kw    = (row.get("keyword") or "").strip()
    h1_raw       = (row.get("h1") or "").strip()
    h1           = "" if h1_raw.lower() == "none" else h1_raw
    page_type    = row.get("page_type") or "general"

    def _empty(status: str) -> dict:
        return {
            "url": url, "selected_keyword": None, "keyword_source": status,
            "runner_up": None, "kw_volume": None, "kw_difficulty": None,
            "generated_title": None, "generated_description": None,
            "optimised_h1": None, "title_length": None,
            "description_length": None, "h1_length": None,
            "review_notes": "",
            "status": status,
        }

    if not url or not url.startswith("http"):
        return _empty("skipped: invalid URL")

    # ── Keyword selection ──────────────────────────────────────────────────
    keyword = manual_kw
    keyword_source = "manual" if manual_kw else None
    runner_up = ""
    kw_volume = None
    kw_difficulty = None

    if not keyword and gsc_client and settings.get("use_gsc") and settings.get("site_url"):
        step("fetching GSC data...")
        gsc_queries = get_top_queries_for_url(
            gsc_client, settings["site_url"], url, top_n=10
        )
        error_check = gsc_queries[0] if gsc_queries else {}

        if error_check.get("_error"):
            keyword_source = f"fallback: GSC error - {error_check['_error'][:120]}"
            step("✗ GSC error — " + error_check["_error"][:80])
        elif gsc_queries:
            step("GSC: " + str(len(gsc_queries)) + " quer" + ("y" if len(gsc_queries) == 1 else "ies") + " found")
            query_list = [q["query"] for q in gsc_queries]

            step("fetching DataForSEO keyword data...")
            try:
                dfs_volumes = get_keyword_overview(settings["dfs_login"], settings["dfs_password"], query_list, location_code=settings.get("location_code", 2840))
                dfs_difficulty = get_keyword_difficulty(settings["dfs_login"], settings["dfs_password"], query_list, location_code=settings.get("location_code", 2840))
            except Exception as dfs_error:
                dfs_volumes = {}
                dfs_difficulty = {}
                keyword_source = f"fallback: DataForSEO error - {str(dfs_error)[:120]}"
                step("DataForSEO keyword lookup failed - " + str(dfs_error)[:120])

            dfs_merged = {}
            has_dfs_volume = False
            for kw in query_list:
                kl = kw.lower()
                vol  = dfs_volumes.get(kl, {}).get("volume", 0)
                diff = dfs_difficulty.get(kl, {}).get("difficulty", 50)
                dfs_merged[kl] = {"volume": vol, "difficulty": diff}
                if vol > 0:
                    has_dfs_volume = True

            selection = select_keyword(
                gsc_queries=gsc_queries,
                dfs_data=dfs_merged,
                branded_terms=branded_terms,
                min_volume=settings.get("min_volume", 10),
                h1=h1,
                restricted_industry=settings.get("restricted_industry", False),
            )

            if selection.get("selected_keyword"):
                keyword        = selection["selected_keyword"]
                keyword_source = "gsc+dfs" if has_dfs_volume else "gsc-only (low DFS volume)"
                runner_up      = selection["runner_up"]["keyword"] if selection.get("runner_up") else ""
                kw_volume      = (selection.get("selected_keyword_data") or {}).get("volume")
                kw_difficulty  = (selection.get("selected_keyword_data") or {}).get("difficulty")
                step("keyword selected: " + str(keyword) + " [" + str(keyword_source) + "]" + (", vol:" + str(kw_volume) if kw_volume else ""))
            elif selection.get("fallback_triggered"):
                non_branded = [q for q in gsc_queries if not any(b in q["query"].lower() for b in branded_terms) and q.get("position", 99) > 1.0]
                if non_branded:
                    top_gsc = sorted(non_branded, key=lambda x: x.get("impressions", 0), reverse=True)[0]
                    keyword        = top_gsc["query"]
                    keyword_source = "gsc-only (low DFS volume)"
                    runner_up      = non_branded[1]["query"] if len(non_branded) > 1 else ""
                    step("keyword selected: " + str(keyword) + " [GSC fallback, low vol]")
                else:
                    keyword_source = "fallback: no keyword passed scoring"
                    step("⚠ no keyword passed scoring")
            else:
                keyword_source = "fallback: no GSC data"
                step("⚠ no GSC data returned")
        else:
            keyword_source = "fallback: no GSC data"
            step("⚠ no GSC data returned")
    elif not keyword:
        keyword_source = "skipped: GSC disabled and no keyword provided"

    if not keyword:
        step("✗ no keyword — skipping AI call")
        return {**_empty(f"skipped: {keyword_source}"), "keyword_source": keyword_source}

    # Track used keywords
    if keyword.lower() in used_keywords:
        keyword_source += " (duplicate — reused)"
    used_keywords.add(keyword.lower())

    # ── Generate copy ──────────────────────────────────────────────────────
    step("generating meta copy with " + settings.get("provider", "Claude") + "...")

    brand_guidelines = ""
    if brand_profile:
        parts = []
        if brand_profile.get("tone_of_voice"):
            parts.append("Tone of voice: " + brand_profile["tone_of_voice"])
        if brand_profile.get("key_messages"):
            parts.append("Key messages: " + brand_profile["key_messages"])
        if brand_profile.get("words_to_avoid"):
            parts.append("Words to avoid: " + brand_profile["words_to_avoid"])
        if brand_profile.get("guidelines"):
            parts.append(brand_profile["guidelines"])
        brand_guidelines = "\n".join(parts)

    _niche_ctx = get_niche_context(settings.get("niche", ""))
    if _niche_ctx:
        brand_guidelines = (brand_guidelines + "\n\n" + _niche_ctx).strip()

    try:
        copy = generate_copy(
            provider=settings.get("provider", "Claude"),
            api_key=settings.get("api_key", ""),
            url=url,
            keyword=keyword,
            page_type=page_type,
            brand_name=settings.get("brand_name", "") if settings.get("include_brand", True) else "",
            forbidden_phrases=settings.get("forbidden_phrases", ""),
            context=brand_guidelines,
            business_type=settings.get("business_type", "general"),
            h1=h1,
            model=settings.get("model", ""),
        )
        title       = copy.get("title", "")
        description = copy.get("description", "")
        h1_opt      = copy.get("h1_optimised", "")
        review_notes = copy.get("review_notes", "")
        step("✓ meta copy generated — title: " + str(len(title)) + " chars, desc: " + str(len(description)) + " chars")

        return {
            "url":                  url,
            "h1_input":             h1,
            "selected_keyword":     keyword,
            "keyword_source":       keyword_source,
            "runner_up":            runner_up,
            "kw_volume":            kw_volume,
            "kw_difficulty":        kw_difficulty,
            "generated_title":      title,
            "generated_description": description,
            "optimised_h1":         h1_opt,
            "title_length":         len(title),
            "description_length":   len(description),
            "h1_length":            len(h1_opt),
            "review_notes":         review_notes,
            "status":               "ok",
        }
    except Exception as e:
        step("✗ generation failed — " + str(e)[:80])
        return {
            **_empty(f"error: {str(e)}"),
            "selected_keyword": keyword,
            "keyword_source":   keyword_source,
            "runner_up":        runner_up,
            "kw_volume":        kw_volume,
            "kw_difficulty":    kw_difficulty,
        }


def _process_job(job_id: str, rows: list, settings: dict, sa_info: dict, brand_profile: dict = None):
    sb = get_supabase()
    delay = _RATE_LIMITS.get(settings.get("provider", "Claude"), 1.0)
    total = len(rows)

    _update_job(sb, job_id, {
        "status":      "running",
        "total_rows":  total,
        "current_step": "Starting...",
    })

    # Init GSC
    gsc_client = None
    if settings.get("use_gsc") and sa_info:
        try:
            gsc_client = get_gsc_client(sa_info)
        except Exception as e:
            _update_job(sb, job_id, {"error": f"GSC auth failed: {e}"})

    import re as _re
    branded_terms = [b.strip() for b in settings.get("brand_name", "").split() if b.strip()]
    full_brand = settings.get("full_brand_name", "").strip()
    if full_brand:
        branded_terms = list(set(branded_terms + [w.lower() for w in _re.findall(r"[a-zA-Z]+", full_brand) if len(w) >= 3]))
    branded_input = settings.get("branded_terms_input", "").strip()
    if branded_input:
        branded_terms = list(set(branded_terms + [t.strip().lower() for t in branded_input.splitlines() if t.strip()]))

    used_keywords: set = set()
    results = []

    for idx, row in enumerate(rows):
        url = (row.get("url") or "").strip()
        _update_job(sb, job_id, {"current_step": f"Row {idx+1}/{total}: starting — {url}"})

        if _is_cancelled(sb, job_id):
            _update_job(sb, job_id, {
                "status":        "cancelled",
                "current_step":  f"Cancelled after {idx}/{total} rows.",
                "failed_rows":   sum(1 for r in results if r.get("error") or r.get("status") == "error"),
            })
            return

        result = _process_single_row(
            row=row,
            settings=settings,
            gsc_client=gsc_client,
            branded_terms=branded_terms,
            used_keywords=used_keywords,
            sb=sb,
            job_id=job_id,
            row_num=idx + 1,
            total_rows=total,
            brand_profile=brand_profile,
        )
        results.append(result)

        _update_job(sb, job_id, {
            "completed_rows": idx + 1,
            "results": results,
        })

        if _is_cancelled(sb, job_id):
            _update_job(sb, job_id, {
                "status":       "cancelled",
                "current_step": f"Cancelled after {idx + 1}/{total} rows.",
                "failed_rows":  sum(1 for r in results if r.get("error") or r.get("status") == "error"),
            })
            return

        if idx < len(rows) - 1:
            time.sleep(delay)

    if _is_cancelled(sb, job_id):
        _update_job(sb, job_id, {
            "status":       "cancelled",
            "current_step": "Cancelled.",
            "failed_rows":  sum(1 for r in results if r.get("error") or r.get("status") == "error"),
            "results":      results,
        })
        return

    _update_job(sb, job_id, {
        "status":        "complete",
        "current_step":  "Done.",
        "completed_rows": len(results),
        "failed_rows":   sum(1 for r in results if r.get("error") or r.get("status") == "error"),
        "results":       results,
    })


# ── Request model ──────────────────────────────────────────────────────────────
class MetaRow(BaseModel):
    url: str
    keyword: str = ""
    page_type: str = "general"
    h1: str = ""


class MetaSettings(BaseModel):
    niche: str = ""
    provider: str = "Claude"
    api_key: str = ""
    dfs_login: str = ""
    dfs_password: str = ""
    business_type: str = "general"
    brand_name: str = ""
    full_brand_name: str = ""
    include_brand: bool = True
    forbidden_phrases: str = ""
    branded_terms_input: str = ""
    location_code: int = 2840
    min_volume: int = 10
    use_gsc: bool = True
    site_url: str = ""
    brand_profile_id: str = ""
    restricted_industry: bool = False
    model: str = ""


class MetaJobRequest(BaseModel):
    name: str = ""
    rows: list[MetaRow]
    settings: MetaSettings


@router.post("/run")
def run_meta_job(
    request: MetaJobRequest,
    background_tasks: BackgroundTasks,
    user=Depends(get_current_user),
    sb=Depends(get_supabase),
):
    job_id = str(uuid.uuid4())

    # Fetch GSC service account
    sa_info = None
    if request.settings.use_gsc:
        try:
            sa_res = sb.table("user_settings").select("gsc_service_account").eq("user_id", user.id).execute()
            if sa_res.data and sa_res.data[0].get("gsc_service_account"):
                sa_info = sa_res.data[0]["gsc_service_account"]
        except Exception:
            pass

    # Fetch brand profile
    brand_profile = None
    if request.settings.brand_profile_id:
        try:
            bp_res = sb.table("brand_profiles").select("data").eq("id", request.settings.brand_profile_id).eq("user_id", user.id).execute()
            if bp_res.data:
                brand_profile = bp_res.data[0].get("data") or {}
        except Exception:
            pass

    sb.table("jobs").insert({
        "id":            job_id,
        "user_id":       user.id,
        "name":          request.name or f"Meta job {len(request.rows)} URLs",
        "tool":          "meta",
        "status":        "pending",
        "total_rows":    len(request.rows),
        "completed_rows": 0,
        "failed_rows":   0,
        "results":       [],
        "logs":          [],
        "rows":          [r.model_dump() for r in request.rows],
        "settings":      request.settings.model_dump(exclude={"api_key", "dfs_password"}),
        "current_step":  "Queued...",
    }).execute()

    background_tasks.add_task(
        _process_job,
        job_id=job_id,
        rows=[r.model_dump() for r in request.rows],
        settings=request.settings.model_dump(),
        sa_info=sa_info,
        brand_profile=brand_profile,
    )

    return {"job_id": job_id, "status": "running"}
