from utils.page_types import normalize_page_type


def test_service_aliases_stay_service():
    assert normalize_page_type("service_lp") == "service"
    assert normalize_page_type("Service Landing Page") == "service"
    assert normalize_page_type("service page") == "service"


def test_plain_landing_page_stays_distinct():
    assert normalize_page_type("landing page") == "landing_page"
    assert normalize_page_type("LP") == "landing_page"


def test_meta_specific_aliases_match_existing_prompt_types():
    assert normalize_page_type("category page") == "category"
    assert normalize_page_type("collection page") == "category"
    assert normalize_page_type("city page") == "location"
    assert normalize_page_type("about us") == "brand"
    assert normalize_page_type("", default="general") == "general"
