from datetime import datetime, timezone


SECRET_FIELDS = frozenset({
    "api_key",
    "dfs_password",
    "jina_api_key",
    "gsc_service_account",
    "_gsc_credentials",
    "_gsc_service_account",
})
_MISSING_SCHEMA_CODES = frozenset({"PGRST204", "PGRST205", "42P01", "42703"})


def strip_secret_fields(settings: dict | None) -> dict:
    return {key: value for key, value in (settings or {}).items() if key not in SECRET_FIELDS}


def split_provider_settings(settings: dict | None) -> tuple[dict, dict]:
    safe = strip_secret_fields(settings)
    secrets = {key: value for key, value in (settings or {}).items() if key in SECRET_FIELDS and value not in (None, "")}
    return safe, secrets


def _read_row(sb, table: str, user_id: str, allow_missing_schema: bool = False) -> dict:
    try:
        result = sb.table(table).select("provider_settings, gsc_service_account").eq("user_id", user_id).execute()
        return result.data[0] if result.data else {}
    except Exception as exc:
        if allow_missing_schema and getattr(exc, "code", None) in _MISSING_SCHEMA_CODES:
            return {}
        raise


def load_user_credentials(sb, user_id: str) -> dict:
    legacy = _read_row(sb, "user_settings", user_id)
    server_only = _read_row(sb, "user_credentials", user_id, allow_missing_schema=True)
    provider_settings = dict(legacy.get("provider_settings") or {})
    provider_settings.update(server_only.get("provider_settings") or {})
    return {"provider_settings": provider_settings, "gsc_service_account": server_only.get("gsc_service_account") or legacy.get("gsc_service_account")}


def load_active_gsc_credentials(sb, user_id: str) -> dict | None:
    result = sb.table("user_settings").select("gsc_auth_method").eq("user_id", user_id).execute()
    row = result.data[0] if result.data else {}
    method = row.get("gsc_auth_method", "service_account")

    if method == "google_oauth":
        result = (
            sb.table("gsc_oauth_connections")
            .select("refresh_token_ciphertext,status")
            .eq("user_id", user_id)
            .execute()
        )
        connection = result.data[0] if result.data else {}
        ciphertext = connection.get("refresh_token_ciphertext")
        if connection.get("status") != "connected" or not ciphertext:
            return None
        return {"method": "google_oauth", "refresh_token_ciphertext": ciphertext}

    if method != "service_account":
        return None

    service_account = load_user_credentials(sb, user_id).get("gsc_service_account")
    if not service_account:
        return None
    return {"method": "service_account", "service_account": service_account}


def hydrate_job_settings(sb, user_id: str, settings: dict | None) -> dict:
    hydrated = strip_secret_fields(settings)
    credentials = load_user_credentials(sb, user_id)
    stored = credentials.get("provider_settings") or {}
    for key in {"api_key", "dfs_password", "jina_api_key"}:
        if stored.get(key):
            hydrated[key] = stored[key]
    active = load_active_gsc_credentials(sb, user_id)
    if active:
        hydrated["_gsc_credentials"] = active
    return hydrated


def mark_gsc_reconnect_required(sb, user_id: str, refresh_token_ciphertext: str) -> bool:
    try:
        result = (
            sb.table("gsc_oauth_connections")
            .update({
                "status": "reconnect_required",
                "last_error_code": "refresh_failed",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            })
            .eq("user_id", user_id)
            .eq("status", "connected")
            .eq("refresh_token_ciphertext", refresh_token_ciphertext)
            .execute()
        )
        return bool(result.data)
    except Exception:
        return False

def save_server_credentials(sb, user_id: str, provider_settings: dict | None = None, gsc_service_account=None) -> bool:
    data = {"user_id": user_id, "updated_at": "now()"}
    if provider_settings is not None:
        data["provider_settings"] = provider_settings
    if gsc_service_account is not None:
        data["gsc_service_account"] = gsc_service_account
    try:
        sb.table("user_credentials").upsert(data, on_conflict="user_id").execute()
        return True
    except Exception:
        return False


def clear_server_credential_field(sb, user_id: str, field: str) -> bool:
    try:
        value = {} if field == "provider_settings" else None
        sb.table("user_credentials").update({field: value}).eq("user_id", user_id).execute()
        return True
    except Exception:
        return False
