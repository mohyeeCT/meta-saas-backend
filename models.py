from pydantic import BaseModel
from typing import Optional


class JobRow(BaseModel):
    url: str
    keyword: Optional[str] = ""
    page_type: Optional[str] = "general"
    h1: Optional[str] = ""


class JobSettings(BaseModel):
    # AI provider
    provider: str = "Claude"
    model: Optional[str] = None
    api_key: str

    # Copy config
    business_type: str = "general"
    brand_name: str = ""
    full_brand_name: str = ""  # abbreviation expansion
    brand_profile_id: str = ""  # ID from brand_profiles table e.g. "Dayson Shalabi Burkert" for DSB
    num_faqs: int = 5
    forbidden_phrases: str = ""
    restricted_industry: bool = False  # Score on GSC signals only when DFS suppresses volume
    branded_terms_input: str = ""  # manual branded terms to exclude, one per line
    # Batching
    batch_size: int = 5  # 1 = per-page mode, >1 = group pages into single AI calls
    load_async_ai_overview: bool = True  # fetch async AI Overview (doubles DFS cost for that call)

    # DataForSEO
    dfs_login: str
    dfs_password: str
    location_code: int = 2840

    # Scraping
    jina_api_key: str = ""
    scrape_pages: bool = True

    # GSC
    use_gsc: bool = True
    site_url: str = ""
    min_volume: int = 10


class RunJobRequest(BaseModel):
    name: str
    rows: list[JobRow]
    settings: JobSettings


class FAQItem(BaseModel):
    question: str
    answer: str
    source: str  # ai_overview | paa | generated


class RowResult(BaseModel):
    url: str
    keyword: str
    keyword_source: str  # gsc | manual | fallback
    faqs: list[FAQItem]
    schema_json: str
    schema_script: str
    error: Optional[str] = None
