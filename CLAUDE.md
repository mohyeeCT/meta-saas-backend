# meta-saas-backend — Repo Context

See `../CLAUDE.md` for full platform context, conventions, and working rules.

## What This Repo Is

FastAPI backend for the Meta Copy workflow.
Deployed on Railway EU West. Default branch: `main`. Current HEAD: `c446341`.
Runtime: Python 3.12.

Railway URL: `https://meta-saas-backend-production.up.railway.app`

## File Structure

```
main.py           — App, CORS, router mounts, global exception handler
auth.py           — Supabase token validation
models.py         — Pydantic models
routers/
  meta.py         — POST /api/meta/run + _process_single_row
  jobs.py         — Shared job CRUD
  settings.py     — Shared settings CRUD
utils/
  copy_gen.py     — generate_copy (structured JSON), sanitise, PROVIDER_FN
  dfs.py          — Keyword volume, difficulty, SERP
  gsc.py          — GSC queries
  keyword.py      — select_keyword
  niches.py       — get_niche_context (23 niches)
schema.sql        — Reference schema including brand_profiles
tests/
  test_cors.py
  test_dfs_error_visibility.py
  test_keyword.py
  test_meta_parity.py
  test_model_selection.py
```

## Endpoints

Same shared set as FAQ with POST /api/meta/run as the tool endpoint.

## Meta Pipeline (_process_single_row)

1. Inject niche context into brand_guidelines
2. Select primary keyword: manual → GSC → DFS → H1 fallback
3. Merge brand profile into guidelines
4. Single structured JSON generation call: generate_copy
5. Parse JSON: extract title, description, h1, review_notes
6. Apply relaxed length guidance: title aims for about 50 to 80 chars,
   description aims for about 140 to 180 chars, H1 still aims for under 70
7. Apply claim guardrails (no absolute claims without evidence)
8. Sanitise all three outputs
9. Write to Supabase

Manual keyword rule: row-level manual keywords are explicit user input and
should stay the primary choice when present. GSC/DFS/H1 fallback should fill
gaps, not override manual intent.

## Generation Approach

Single structured JSON call per row. The AI returns one object with all three
fields plus optional review_notes. More reliable than three separate calls.

## Key Model Fields

niche, business_type, provider, model, brand_name, include_brand,
forbidden_phrases, brand_profile_id, restricted_industry

## Known Gotchas

- `landing_page` is distinct from service pages. Service landing page aliases
  normalize to service behavior; plain landing-page aliases normalize to
  `landing_page`.

- Niche context goes into brand_guidelines (not page_context — Meta has no scraping).
- Parse AI JSON with try/except. Strip markdown fences before json.loads.
- H1 must never contain the brand name — enforced in prompt hard rules.
- Length normalization is post-processing. Trim only when title exceeds 80
  chars or description exceeds 180 chars.
- `model` is NOT excluded from `model_dump(exclude={"api_key","dfs_password"})` — it IS
  stored in the job settings JSON in Supabase and survives reruns correctly.
- `_rerun_single_row` in `jobs.py` uses a deferred `from routers.meta import
  _process_single_row, _update_job` inside the function body (not at module level)
  to avoid circular imports. Do not move it to the top of the file.
- `get_keyword_difficulty` calls `dataforseo_labs/google/bulk_keyword_difficulty/live`.
  The API nests results at `tasks[].result[].items[]` — one level deeper than the
  volume endpoint. The parsing loop must go three levels deep (tasks → result → items)
  to read `keyword_difficulty`. A previous bug iterated `result[]` directly, causing
  all lookups to return the empty-string key and difficulty to silently default to 50.
  `kw_difficulty` is tracked through `_process_single_row` and included in all
  result dicts and `_empty`.


## Local Dev Setup

Tests require FastAPI and all backend dependencies. Without a venv, `pytest`
will fail on collection with `ModuleNotFoundError: No module named 'fastapi'`.

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt pytest
python -m pytest tests/ -v
```

CI (GitHub Actions) installs dependencies automatically — this setup is only
needed for local test runs.
