import os
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials as OAuthCredentials
from google.oauth2.service_account import Credentials as ServiceAccountCredentials
from googleapiclient.discovery import build
from datetime import datetime, timedelta

from gsc_crypto import decrypt_secret

GSC_SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]
TOKEN_URI = "https://oauth2.googleapis.com/token"
_INVALID_CREDENTIALS = "Invalid GSC credentials"


def get_gsc_client(credentials_envelope: dict):
    if not isinstance(credentials_envelope, dict):
        raise ValueError(_INVALID_CREDENTIALS)

    method = credentials_envelope.get("method")
    if method == "service_account":
        service_account = credentials_envelope.get("service_account")
        if not isinstance(service_account, dict) or not service_account:
            raise ValueError(_INVALID_CREDENTIALS)
        creds = ServiceAccountCredentials.from_service_account_info(service_account, scopes=GSC_SCOPES)
    elif method == "google_oauth":
        ciphertext = credentials_envelope.get("refresh_token_ciphertext")
        if not isinstance(ciphertext, str) or not ciphertext:
            raise ValueError(_INVALID_CREDENTIALS)
        client_id = os.environ["GOOGLE_OAUTH_CLIENT_ID"]
        client_secret = os.environ["GOOGLE_OAUTH_CLIENT_SECRET"]
        refresh_token = decrypt_secret(ciphertext)
        creds = OAuthCredentials(
            token=None,
            refresh_token=refresh_token,
            token_uri=TOKEN_URI,
            client_id=client_id,
            client_secret=client_secret,
            scopes=GSC_SCOPES,
        )
        creds.refresh(Request())
    else:
        raise ValueError(_INVALID_CREDENTIALS)

    return build("searchconsole", "v1", credentials=creds)


def get_top_queries_for_url(gsc_client, site_url: str, page_url: str, top_n: int = 10) -> list:
    """Pull top N queries for a given URL from GSC (90-day window).
    Returns list of dicts: query, clicks, impressions, ctr, position.
    Returns [{"_error": "..."}] on failure so callers can surface errors.
    """
    end_date = datetime.today() - timedelta(days=3)
    start_date = end_date - timedelta(days=90)

    try:
        response = gsc_client.searchanalytics().query(
            siteUrl=site_url,
            body={
                "startDate": start_date.strftime("%Y-%m-%d"),
                "endDate": end_date.strftime("%Y-%m-%d"),
                "dimensions": ["query"],
                "dimensionFilterGroups": [{
                    "filters": [{
                        "dimension": "page",
                        "operator": "equals",
                        "expression": page_url
                    }]
                }],
                "rowLimit": top_n,
                "orderBy": [{"fieldName": "impressions", "sortOrder": "DESCENDING"}]
            }
        ).execute()

        rows = response.get("rows", [])
        return [
            {
                "query": r["keys"][0],
                "clicks": r.get("clicks", 0),
                "impressions": r.get("impressions", 0),
                "ctr": r.get("ctr", 0.0),
                "position": r.get("position", 99.0)
            }
            for r in rows
        ]
    except Exception as e:
        return [{"_error": str(e)}]
