import re

import requests


JINA_BASE = "https://r.jina.ai"
FIRECRAWL_SCRAPE_URL = "https://api.firecrawl.dev/v2/scrape"
_JINA_RENDER_TIMEOUT_SECONDS = 180
_JINA_REQUEST_TIMEOUT_SECONDS = 200
_JINA_CACHE_FALLBACK_TIMEOUT_SECONDS = 30
_FIRECRAWL_RENDER_TIMEOUT_MS = 120000
_FIRECRAWL_REQUEST_TIMEOUT_SECONDS = 135

_REMOVE_SELECTOR = ", ".join([
    "nav", "header", "footer", "aside",
    "#cart", ".cart", "[class*='cart']",
    "#header", "#footer", "#nav", "#sidebar",
    "[class*='sidebar']", "[class*='navigation']",
    "[class*='breadcrumb']", "[class*='cookie']",
    "[class*='popup']", "[class*='modal']",
    "[class*='newsletter']", "[class*='subscribe']",
    "[class*='related']", "[class*='recommended']",
    "[class*='upsell']", "[class*='cross-sell']",
    "form", "script", "style", "noscript", "iframe",
])

_NOISE_LINE_PATTERNS = re.compile(
    r"^\s*("
    r"\$[\d,.]+|"
    r"Add to cart|Sold out|Sale price|"
    r"Regular price|Unit price|"
    r"Quantity must be|Adding product|"
    r"Please allow \d|"
    r"Pickup available|Usually ready|"
    r"Check availability|Service Center|"
    r"Skip to content|Log in|Sign in|"
    r"Search$|Menu$|Close$|"
    r"This page does not seem to contain|"
    r"\+?1?[\s\-.]?\(?\d{3}\)?[\s\-.]?\d{3}[\s\-.]?\d{4}"
    r")\s*$",
    re.IGNORECASE,
)


def _score_paragraph(paragraph: str) -> float:
    words = paragraph.split()
    if len(words) < 8:
        return 0.0
    if len(re.findall(r"\[.+?\]\(https?://", paragraph)) > 2:
        return 0.0
    alpha_ratio = sum(character.isalpha() for character in paragraph) / max(len(paragraph), 1)
    if alpha_ratio < 0.5:
        return 0.0
    return len(words) * alpha_ratio


def _process_reader_text(text: str, max_chars: int) -> dict:
    text = (text or "").strip()
    if not text:
        return {"content": "", "title": "", "success": False, "error": "Jina returned empty content"}

    title_match = re.search(r"^Title:\s*(.+)$", text, re.MULTILINE)
    title = title_match.group(1).strip() if title_match else ""
    text = re.sub(r"!\[.*?\]\(.*?\)", "", text)
    text = re.sub(r"^\s*\*\s+\[.+?\]\(https?://.+?\)\s*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"^#{1,4}\s+\[.+?\]\(https?://.+?\)\s*$", "", text, flags=re.MULTILINE)
    lines = [line for line in text.splitlines() if not _NOISE_LINE_PATTERNS.match(line)]
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    if not text:
        return {"content": "", "title": title, "success": False, "error": "No content found after stripping boilerplate"}

    result_paragraphs = []
    chars_used = 0
    for paragraph in re.split(r"\n{2,}", text):
        if chars_used >= max_chars:
            break
        if _score_paragraph(paragraph) > 0 or paragraph.strip().startswith("#"):
            result_paragraphs.append(paragraph)
            chars_used += len(paragraph)

    content = "\n\n".join(result_paragraphs).strip()
    if len(content) > max_chars:
        truncated = content[:max_chars]
        last_period = truncated.rfind(".")
        content = truncated[:last_period + 1].strip() if last_period > max_chars * 0.5 else truncated.strip()
    if not content:
        return {"content": "", "title": title, "success": False, "error": "No substantive content found after scoring"}
    return {"content": content, "title": title, "success": True, "error": ""}


def _request_cached_snapshot(url: str, headers: dict):
    fallback_headers = dict(headers)
    fallback_headers.pop("X-No-Cache", None)
    fallback_headers.pop("X-Remove-Selector", None)
    fallback_headers.pop("X-Timeout", None)
    return requests.get(
        f"{JINA_BASE}/{url}",
        headers=fallback_headers,
        timeout=_JINA_CACHE_FALLBACK_TIMEOUT_SECONDS,
    )


def scrape_page_context(api_key: str, url: str, max_chars: int = 10000) -> dict:
    if not url:
        return {"content": "", "title": "", "success": False, "error": "No URL provided"}
    headers = {
        "Accept": "text/plain",
        "X-Return-Format": "markdown",
        "X-With-Links-Summary": "false",
        "X-With-Images-Summary": "false",
        "X-Remove-Selector": _REMOVE_SELECTOR,
        "X-No-Cache": "true",
        "X-Timeout": str(_JINA_RENDER_TIMEOUT_SECONDS),
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    response_source = "live"
    fallback_attempted = False
    try:
        try:
            response = requests.get(f"{JINA_BASE}/{url}", headers=headers, timeout=_JINA_REQUEST_TIMEOUT_SECONDS)
            if response.status_code in (400, 422):
                headers.pop("X-Remove-Selector", None)
                response = requests.get(f"{JINA_BASE}/{url}", headers=headers, timeout=_JINA_REQUEST_TIMEOUT_SECONDS)
        except requests.exceptions.Timeout:
            fallback_attempted = True
            response_source = "cached_fallback"
            response = _request_cached_snapshot(url, headers)

        if response_source == "live" and 500 <= response.status_code < 600:
            fallback_attempted = True
            response_source = "cached_fallback"
            response = _request_cached_snapshot(url, headers)

        response.raise_for_status()
        result = _process_reader_text(response.text, max_chars)
        if not result["success"] and response_source == "live":
            fallback_attempted = True
            cached_response = _request_cached_snapshot(url, headers)
            cached_response.raise_for_status()
            cached_result = _process_reader_text(cached_response.text, max_chars)
            if cached_result["success"]:
                result = cached_result
                response_source = "cached_fallback"
        result["source"] = response_source
        return result
    except requests.exceptions.Timeout:
        suffix = " after cached fallback" if fallback_attempted else ""
        return {"content": "", "title": "", "success": False, "error": f"Request timed out{suffix}"}
    except requests.exceptions.HTTPError as error:
        suffix = " after cached fallback" if fallback_attempted else ""
        return {"content": "", "title": "", "success": False, "error": f"HTTP {error.response.status_code}{suffix}"}
    except requests.exceptions.RequestException as error:
        return {"content": "", "title": "", "success": False, "error": str(error)}
    except Exception as error:
        return {"content": "", "title": "", "success": False, "error": str(error)}


def _firecrawl_failure(message: str) -> dict:
    return {"content": "", "title": "", "success": False, "error": message, "source": "firecrawl"}


def _firecrawl_http_error(status_code: int) -> str:
    if status_code in (401, 403):
        return "Firecrawl authentication failed. Update the API key in Settings."
    if status_code == 402:
        return "Firecrawl credits are unavailable. Check the Firecrawl account."
    if status_code == 429:
        return "Firecrawl rate limit reached. Try again later."
    if status_code >= 500:
        return "Firecrawl is temporarily unavailable. Try again later."
    return "Firecrawl could not scrape this page."


def scrape_page_context_firecrawl(api_key: str, url: str, max_chars: int = 10000) -> dict:
    if not url:
        return _firecrawl_failure("No URL provided")
    if not api_key:
        return _firecrawl_failure("Firecrawl API key is not configured.")
    try:
        response = requests.post(
            FIRECRAWL_SCRAPE_URL,
            json={
                "url": url,
                "formats": ["markdown"],
                "onlyMainContent": True,
                "onlyCleanContent": False,
                "maxAge": 0,
                "waitFor": 0,
                "timeout": _FIRECRAWL_RENDER_TIMEOUT_MS,
                "removeBase64Images": True,
                "blockAds": True,
                "proxy": "auto",
                "storeInCache": False,
            },
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            timeout=_FIRECRAWL_REQUEST_TIMEOUT_SECONDS,
        )
    except requests.exceptions.Timeout:
        return _firecrawl_failure("Firecrawl request timed out.")
    except requests.exceptions.RequestException:
        return _firecrawl_failure("Firecrawl could not be reached. Try again later.")

    if not 200 <= response.status_code < 300:
        return _firecrawl_failure(_firecrawl_http_error(response.status_code))
    try:
        response_body = response.json()
    except ValueError:
        return _firecrawl_failure("Firecrawl returned an invalid response.")
    page_data = response_body.get("data") if isinstance(response_body, dict) else None
    if not isinstance(response_body, dict) or response_body.get("success") is not True or not isinstance(page_data, dict):
        return _firecrawl_failure("Firecrawl could not scrape this page.")
    markdown = (page_data.get("markdown") or "").strip()
    if not markdown:
        return _firecrawl_failure("Firecrawl returned empty content.")
    metadata = page_data.get("metadata") or {}
    metadata_title = metadata.get("title", "") if isinstance(metadata, dict) else ""
    reader_text = f"Title: {metadata_title}\n\n{markdown}" if metadata_title else markdown
    result = _process_reader_text(reader_text, max_chars)
    if metadata_title and not result.get("title"):
        result["title"] = metadata_title
    result["source"] = "firecrawl"
    return result
