from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from auth import get_current_user, get_supabase

router = APIRouter()


class SettingsUpsert(BaseModel):
    gsc_service_account: Optional[dict] = None
    provider_settings: Optional[dict] = None
    brand_profile: Optional[dict] = None


@router.get("")
def get_settings(user=Depends(get_current_user)):
    """Return user settings. Sensitive keys are never returned - only metadata."""
    sb = get_supabase()
    res = (
        sb.table("user_settings")
        .select("gsc_service_account, provider_settings, brand_profile, updated_at")
        .eq("user_id", user.id)
        .execute()
    )
    if not res.data:
        return {"gsc_service_account": None, "provider_settings": None, "brand_profile": None}

    row = res.data[0]

    # GSC - return metadata only
    sa = row.get("gsc_service_account")
    sa_safe = None
    if sa:
        sa_safe = {
            "client_email": sa.get("client_email"),
            "project_id": sa.get("project_id"),
            "configured": True,
        }

    # Provider settings
    ps = row.get("provider_settings") or {}
    ps_safe = None
    if ps:
        ps_safe = {
            "provider": ps.get("provider", ""),
            "has_api_key": bool(ps.get("api_key")),
            "dfs_login": ps.get("dfs_login", ""),
            "has_dfs_password": bool(ps.get("dfs_password")),
            "has_jina_key": bool(ps.get("jina_api_key")),
            "site_url": ps.get("site_url", ""),
        }

    # Brand profile - returned in full (not sensitive)
    brand_profile = row.get("brand_profile") or {}

    return {
        "gsc_service_account": sa_safe,
        "provider_settings": ps_safe,
        "brand_profile": brand_profile if brand_profile else None,
        "updated_at": row.get("updated_at"),
    }


@router.put("")
def upsert_settings(body: SettingsUpsert, user=Depends(get_current_user)):
    """Save or update user settings."""
    sb = get_supabase()

    if body.gsc_service_account:
        required = ["type", "project_id", "private_key", "client_email"]
        missing = [f for f in required if f not in body.gsc_service_account]
        if missing:
            raise HTTPException(
                status_code=400,
                detail=f"Service account JSON missing fields: {', '.join(missing)}"
            )

    # Fetch existing to merge provider_settings
    existing = sb.table("user_settings").select("provider_settings, gsc_service_account, brand_profile").eq("user_id", user.id).execute()
    existing_row = existing.data[0] if existing.data else {}

    data = {"user_id": user.id, "updated_at": "now()"}

    if body.gsc_service_account is not None:
        data["gsc_service_account"] = body.gsc_service_account
    elif "gsc_service_account" in existing_row:
        data["gsc_service_account"] = existing_row["gsc_service_account"]

    if body.provider_settings is not None:
        # Merge with existing so partial updates don't wipe other fields
        existing_ps = existing_row.get("provider_settings") or {}
        merged = {**existing_ps, **{k: v for k, v in body.provider_settings.items() if v is not None and v != ""}}
        data["provider_settings"] = merged
    elif "provider_settings" in existing_row:
        data["provider_settings"] = existing_row["provider_settings"]

    if body.brand_profile is not None:
        data["brand_profile"] = body.brand_profile
    elif "brand_profile" in existing_row:
        data["brand_profile"] = existing_row["brand_profile"]

    res = sb.table("user_settings").upsert(data, on_conflict="user_id").execute()
    if not res.data:
        raise HTTPException(status_code=500, detail="Failed to save settings")

    return {"saved": True}


@router.get("/provider-credentials")
def get_provider_credentials(user=Depends(get_current_user)):
    """Return full provider credentials for pre-filling job form. Includes api_key."""
    sb = get_supabase()
    res = sb.table("user_settings").select("provider_settings, brand_profile").eq("user_id", user.id).execute()
    if not res.data:
        return {}
    ps = res.data[0].get("provider_settings") or {}
    bp = res.data[0].get("brand_profile") or {}
    return {
        "provider": ps.get("provider", ""),
        "api_key": ps.get("api_key", ""),
        "dfs_login": ps.get("dfs_login", ""),
        "dfs_password": ps.get("dfs_password", ""),
        "jina_api_key": ps.get("jina_api_key", ""),
        "site_url": ps.get("site_url", ""),
        "brand_name": bp.get("brand_name", ""),
    }


@router.delete("/gsc")
def delete_gsc_account(user=Depends(get_current_user)):
    """Remove stored GSC service account."""
    sb = get_supabase()
    sb.table("user_settings").update({"gsc_service_account": None}).eq("user_id", user.id).execute()
    return {"deleted": True}


@router.delete("/credentials")
def delete_credentials(user=Depends(get_current_user)):
    """Remove stored provider credentials."""
    sb = get_supabase()
    sb.table("user_settings").update({"provider_settings": {}}).eq("user_id", user.id).execute()
    return {"deleted": True}


# ── Job Templates ─────────────────────────────────────────────────────────────

class TemplateCreate(BaseModel):
    name: str
    tool: str = "faq"
    settings: dict


@router.get("/templates")
def list_templates(tool: str = "faq", user=Depends(get_current_user)):
    sb = get_supabase()
    res = sb.table("job_templates").select("*").eq("user_id", user.id).eq("tool", tool).order("created_at", desc=True).execute()
    return res.data or []


@router.post("/templates")
def create_template(body: TemplateCreate, user=Depends(get_current_user)):
    sb = get_supabase()
    res = sb.table("job_templates").insert({
        "user_id": user.id,
        "tool": body.tool,
        "name": body.name,
        "settings": body.settings,
    }).execute()
    if not res.data:
        raise HTTPException(status_code=500, detail="Failed to save template")
    return res.data[0]


@router.delete("/templates/{template_id}")
def delete_template(template_id: str, user=Depends(get_current_user)):
    sb = get_supabase()
    sb.table("job_templates").delete().eq("id", template_id).eq("user_id", user.id).execute()
    return {"deleted": True}


# ── Brand Profiles ─────────────────────────────────────────────────────────────

class BrandProfileCreate(BaseModel):
    name: str
    data: dict = {}


class BrandProfileUpdate(BaseModel):
    name: Optional[str] = None
    data: Optional[dict] = None


@router.get("/brand-profiles")
def list_brand_profiles(user=Depends(get_current_user)):
    sb = get_supabase()
    res = sb.table("brand_profiles").select("*").eq("user_id", user.id).order("created_at", desc=False).execute()
    return res.data or []


@router.post("/brand-profiles")
def create_brand_profile(body: BrandProfileCreate, user=Depends(get_current_user)):
    sb = get_supabase()
    res = sb.table("brand_profiles").insert({
        "user_id": user.id,
        "name": body.name,
        "data": body.data,
    }).execute()
    if not res.data:
        raise HTTPException(status_code=500, detail="Failed to create brand profile")
    return res.data[0]


@router.put("/brand-profiles/{profile_id}")
def update_brand_profile(profile_id: str, body: BrandProfileUpdate, user=Depends(get_current_user)):
    sb = get_supabase()
    updates = {"updated_at": "now()"}
    if body.name is not None:
        updates["name"] = body.name
    if body.data is not None:
        updates["data"] = body.data
    sb.table("brand_profiles").update(updates).eq("id", profile_id).eq("user_id", user.id).execute()
    return {"updated": True}


@router.delete("/brand-profiles/{profile_id}")
def delete_brand_profile(profile_id: str, user=Depends(get_current_user)):
    sb = get_supabase()
    sb.table("brand_profiles").delete().eq("id", profile_id).eq("user_id", user.id).execute()
    return {"deleted": True}
