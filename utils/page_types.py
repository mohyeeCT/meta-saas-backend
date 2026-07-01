import re


_ALIASES = {
    "service lp": "service",
    "service landing page": "service",
    "service landing pages": "service",
    "service page": "service",
    "service pages": "service",
    "landing page": "landing_page",
    "landing pages": "landing_page",
    "lp": "landing_page",
    "category page": "category",
    "category pages": "category",
    "collection": "category",
    "collection page": "category",
    "collection pages": "category",
    "ecommerce category": "category",
    "ecommerce category page": "category",
    "product page": "product",
    "product pages": "product",
    "location page": "location",
    "local": "location",
    "local page": "location",
    "local service": "location",
    "local service page": "location",
    "city page": "location",
    "about": "brand",
    "about page": "brand",
    "about us": "brand",
    "brand page": "brand",
    "blog page": "blog",
}


def _clean(value: str) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[_\-/]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_page_type(value: str, default: str = "general") -> str:
    cleaned = _clean(value)
    if not cleaned:
        return default
    return _ALIASES.get(cleaned, cleaned.replace(" ", "_"))
