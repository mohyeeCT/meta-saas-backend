SECRET_FIELDS = frozenset({"api_key", "dfs_password", "jina_api_key", "_gsc_service_account"})


def strip_secret_fields(settings: dict | None) -> dict:
    return {key: value for key, value in (settings or {}).items() if key not in SECRET_FIELDS}


def split_provider_settings(settings: dict | None) -> tuple[dict, dict]:
    safe = strip_secret_fields(settings)
    secrets = {key: value for key, value in (settings or {}).items() if key in SECRET_FIELDS and value not in (None, "")}
    return safe, secrets


def _read_row(sb, table: str, user_id: str) -> dict:
    try:
        result = sb.table(table).select("provider_settings, gsc_service_account").eq("user_id", user_id).execute()
        return result.data[0] if result.data else {}
    except Exception:
        return {}


def load_user_credentials(sb, user_id: str) -> dict:
    legacy = _read_row(sb, "user_settings", user_id)
    server_only = _read_row(sb, "user_credentials", user_id)
    provider_settings = dict(legacy.get("provider_settings") or {})
    provider_settings.update(server_only.get("provider_settings") or {})
    return {"provider_settings": provider_settings, "gsc_service_account": server_only.get("gsc_service_account") or legacy.get("gsc_service_account")}


def hydrate_job_settings(sb, user_id: str, settings: dict | None) -> dict:
    hydrated = dict(settings or {})
    credentials = load_user_credentials(sb, user_id)
    stored = credentials.get("provider_settings") or {}
    for key in {"api_key", "dfs_password", "jina_api_key"}:
        if stored.get(key):
            hydrated[key] = stored[key]
    if credentials.get("gsc_service_account"):
        hydrated["_gsc_service_account"] = credentials["gsc_service_account"]
    return hydrated


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
