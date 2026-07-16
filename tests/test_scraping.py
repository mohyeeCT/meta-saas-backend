import unittest
from unittest.mock import Mock, patch

import requests

from routers import meta
from routers.meta import MetaSettings
from utils import scraper


class MetaScrapingTests(unittest.TestCase):
    def test_jina_remains_primary_and_firecrawl_fallback_stays_off(self):
        settings = MetaSettings()

        self.assertEqual(settings.scrape_provider, "jina")
        self.assertFalse(settings.firecrawl_fallback)

    def test_jina_uses_cached_fallback_after_timeout(self):
        cached = Mock(status_code=200)
        cached.text = (
            "Title: Cached Page\n\n"
            "# Cached Page\n\n"
            "This cached snapshot contains enough useful page content for generation."
        )
        cached.raise_for_status.return_value = None

        with patch.object(
            scraper.requests,
            "get",
            side_effect=[requests.exceptions.Timeout(), cached],
        ) as get:
            result = scraper.scrape_page_context("jina-key", "https://example.com")

        self.assertTrue(result["success"])
        self.assertEqual(result["source"], "cached_fallback")
        self.assertEqual(get.call_count, 2)
        self.assertEqual(get.call_args_list[0].kwargs["headers"]["X-Timeout"], "180")
        self.assertEqual(get.call_args_list[0].kwargs["timeout"], 200)
        self.assertEqual(get.call_args_list[1].kwargs["timeout"], 30)
        self.assertNotIn("X-No-Cache", get.call_args_list[1].kwargs["headers"])

    def test_firecrawl_uses_fresh_v2_scrape(self):
        response = Mock(status_code=200)
        response.json.return_value = {
            "success": True,
            "data": {
                "markdown": "# Example Page\n\nThis page contains enough substantive content for generation.",
                "metadata": {"title": "Example Page"},
            },
        }

        with patch.object(scraper.requests, "post", return_value=response) as post:
            result = scraper.scrape_page_context_firecrawl(
                "firecrawl-key",
                "https://example.com",
            )

        self.assertTrue(result["success"])
        self.assertEqual(result["source"], "firecrawl")
        self.assertEqual(post.call_args.args[0], "https://api.firecrawl.dev/v2/scrape")
        self.assertEqual(post.call_args.kwargs["timeout"], 135)
        self.assertEqual(post.call_args.kwargs["json"]["timeout"], 120000)
        self.assertEqual(post.call_args.kwargs["json"]["maxAge"], 0)
        self.assertFalse(post.call_args.kwargs["json"]["storeInCache"])

    def test_primary_firecrawl_skips_jina(self):
        with patch.object(meta, "scrape_page_context") as jina, patch(
            "utils.scraper.scrape_page_context_firecrawl",
            return_value={"success": True, "source": "firecrawl"},
        ) as firecrawl:
            result = meta._scrape_page_for_settings(
                {
                    "scrape_provider": "firecrawl",
                    "jina_api_key": "jina",
                    "firecrawl_api_key": "firecrawl",
                },
                "https://example.com",
            )

        self.assertTrue(result["success"])
        jina.assert_not_called()
        firecrawl.assert_called_once_with("firecrawl", "https://example.com", max_chars=10000)

    def test_firecrawl_fallback_only_runs_after_jina_failure(self):
        firecrawl_success = {"success": True, "content": "Page context", "source": "firecrawl"}
        with patch.object(
            meta,
            "scrape_page_context",
            return_value={"success": False, "error": "Jina failed"},
        ), patch(
            "utils.scraper.scrape_page_context_firecrawl",
            return_value=firecrawl_success,
        ) as firecrawl:
            result = meta._scrape_page_for_settings(
                {
                    "scrape_provider": "jina",
                    "jina_api_key": "jina",
                    "firecrawl_api_key": "firecrawl",
                    "firecrawl_fallback": True,
                },
                "https://example.com",
            )

        self.assertIs(result, firecrawl_success)
        firecrawl.assert_called_once_with("firecrawl", "https://example.com", max_chars=10000)


if __name__ == "__main__":
    unittest.main()
