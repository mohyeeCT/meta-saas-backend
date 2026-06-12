import unittest
from unittest.mock import patch

from routers import meta


class _FakeQuery:
    def select(self, *_args, **_kwargs):
        return self

    def update(self, *_args, **_kwargs):
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def execute(self):
        return type("Resp", (), {"data": []})()


class _FakeSupabase:
    def table(self, *_args, **_kwargs):
        return _FakeQuery()


def _settings():
    return {
        "provider": "Claude",
        "api_key": "key",
        "business_type": "service",
        "dfs_login": "dfs@example.com",
        "dfs_password": "secret",
        "use_gsc": False,
        "site_url": "",
    }


def _generated_copy():
    return {
        "title": "Emergency Plumbing Services",
        "description": "Get reliable emergency plumbing support.",
        "h1_optimised": "Emergency Plumbing Services",
        "review_notes": "",
    }


class MetaH1KeywordFallbackTests(unittest.TestCase):
    def _process(self, row, settings=None, gsc_client=None):
        with patch.object(meta, "get_niche_context", return_value=""), \
             patch.object(meta, "generate_copy", return_value=_generated_copy()) as mock_generate:
            result = meta._process_single_row(
                row=row,
                settings=settings or _settings(),
                gsc_client=gsc_client,
                branded_terms=[],
                used_keywords=set(),
                sb=_FakeSupabase(),
                job_id="job-1",
                row_num=1,
                total_rows=1,
            )
        return result, mock_generate

    def test_uses_h1_when_keyword_blank_and_gsc_disabled(self):
        result, mock_generate = self._process({
            "url": "https://example.com/emergency-plumbing",
            "keyword": "",
            "page_type": "service",
            "h1": "Emergency Plumbing Services",
        })

        self.assertEqual(result["selected_keyword"], "Emergency Plumbing Services")
        self.assertEqual(result["keyword_source"], "h1 fallback")
        self.assertEqual(mock_generate.call_args.kwargs["keyword"], "Emergency Plumbing Services")

    def test_manual_keyword_still_wins_when_gsc_disabled(self):
        result, mock_generate = self._process({
            "url": "https://example.com/emergency-plumbing",
            "keyword": "manual plumbing keyword",
            "page_type": "service",
            "h1": "Emergency Plumbing Services",
        })

        self.assertEqual(result["selected_keyword"], "manual plumbing keyword")
        self.assertEqual(result["keyword_source"], "manual")
        self.assertEqual(mock_generate.call_args.kwargs["keyword"], "manual plumbing keyword")

    def test_skips_when_h1_unavailable_and_gsc_disabled(self):
        for h1 in ("", "none", "NoNe"):
            with self.subTest(h1=h1):
                result, mock_generate = self._process({
                    "url": "https://example.com/no-keyword",
                    "keyword": "",
                    "page_type": "service",
                    "h1": h1,
                })

                self.assertIsNone(result["selected_keyword"])
                self.assertIn("GSC disabled", result["keyword_source"])
                mock_generate.assert_not_called()

    def test_gsc_enabled_no_data_does_not_fall_back_to_h1(self):
        settings = _settings()
        settings["use_gsc"] = True
        settings["site_url"] = "sc-domain:example.com"

        with patch.object(meta, "get_top_queries_for_url", return_value=[]):
            result, mock_generate = self._process(
                {
                    "url": "https://example.com/no-gsc-data",
                    "keyword": "",
                    "page_type": "service",
                    "h1": "Emergency Plumbing Services",
                },
                settings=settings,
                gsc_client=object(),
            )

        self.assertIsNone(result["selected_keyword"])
        self.assertEqual(result["keyword_source"], "fallback: no GSC data")
        mock_generate.assert_not_called()

    def test_gsc_enabled_without_client_does_not_fall_back_to_h1(self):
        settings = _settings()
        settings["use_gsc"] = True
        settings["site_url"] = "sc-domain:example.com"

        result, mock_generate = self._process(
            {
                "url": "https://example.com/no-gsc-client",
                "keyword": "",
                "page_type": "service",
                "h1": "Emergency Plumbing Services",
            },
            settings=settings,
            gsc_client=None,
        )

        self.assertIsNone(result["selected_keyword"])
        self.assertNotEqual(result["keyword_source"], "h1 fallback")
        mock_generate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
