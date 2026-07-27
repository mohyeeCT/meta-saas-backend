import unittest
import sys
import types
from unittest.mock import patch

supabase_stub = types.ModuleType("supabase")
supabase_stub.create_client = lambda *args, **kwargs: None
supabase_stub.Client = object
sys.modules.setdefault("supabase", supabase_stub)

anthropic_stub = types.ModuleType("anthropic")
anthropic_stub.Anthropic = object
sys.modules.setdefault("anthropic", anthropic_stub)

openai_stub = types.ModuleType("openai")
openai_stub.OpenAI = object
sys.modules.setdefault("openai", openai_stub)

google_stub = types.ModuleType("google")
google_genai_stub = types.ModuleType("google.genai")
google_auth_stub = types.ModuleType("google.auth")
google_auth_exceptions_stub = types.ModuleType("google.auth.exceptions")
google_auth_exceptions_stub.RefreshError = RuntimeError
google_stub.genai = google_genai_stub
google_stub.auth = google_auth_stub
google_auth_stub.exceptions = google_auth_exceptions_stub
sys.modules.setdefault("google", google_stub)
sys.modules.setdefault("google.genai", google_genai_stub)
sys.modules.setdefault("google.auth", google_auth_stub)
sys.modules.setdefault("google.auth.exceptions", google_auth_exceptions_stub)

mistralai_stub = types.ModuleType("mistralai")
mistralai_stub.Mistral = object
sys.modules.setdefault("mistralai", mistralai_stub)

groq_stub = types.ModuleType("groq")
groq_stub.Groq = object
sys.modules.setdefault("groq", groq_stub)

gsc_stub = types.ModuleType("utils.gsc")
gsc_stub.GscOAuthConfigError = RuntimeError
gsc_stub.get_gsc_client = lambda *args, **kwargs: None
gsc_stub.get_top_queries_for_url = lambda *args, **kwargs: []
sys.modules.setdefault("utils.gsc", gsc_stub)

copy_gen_stub = types.ModuleType("utils.copy_gen")
copy_gen_stub.generate_copy = lambda *args, **kwargs: {}
sys.modules.setdefault("utils.copy_gen", copy_gen_stub)

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
                user_id="user-1",
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

    def test_generated_title_matching_h1_is_flagged_for_review(self):
        result, _mock_generate = self._process({
            "url": "https://example.com/emergency-plumbing",
            "keyword": "emergency plumbing",
            "page_type": "service",
            "h1": "Emergency Plumbing Services",
        })

        self.assertEqual(result["status"], "review")
        self.assertIn("Generated title matches the input H1.", result["qa_flags"])

    def test_forbidden_phrase_in_meta_output_is_flagged_for_review(self):
        def fake_copy():
            return {
                "title": "Emergency Plumbing Support",
                "description": "Get cheap emergency plumbing support from trained specialists.",
                "h1_optimised": "Emergency Plumbing Help",
                "review_notes": "",
            }

        settings = _settings()
        settings["forbidden_phrases"] = "cheap"
        with patch.object(meta, "get_niche_context", return_value=""), \
             patch.object(meta, "generate_copy", return_value=fake_copy()):
            result = meta._process_single_row(
                row={
                    "url": "https://example.com/emergency-plumbing",
                    "keyword": "emergency plumbing",
                    "page_type": "service",
                    "h1": "Emergency Plumbing Services",
                },
                settings=settings,
                gsc_client=None,
                branded_terms=[],
                used_keywords=set(),
                sb=_FakeSupabase(),
                job_id="job-1",
                user_id="user-1",
                row_num=1,
                total_rows=1,
            )

        self.assertEqual(result["status"], "review")
        self.assertIn('Forbidden phrase found: "cheap".', result["qa_flags"])

    def test_short_present_title_and_description_are_not_flagged_for_review(self):
        flags = meta._meta_qa_flags(
            title="Sale",
            description="Book now.",
            h1_opt="Emergency Plumbing Help",
            input_h1="Emergency Plumbing Services",
            forbidden_phrases=[],
        )

        self.assertEqual(flags, [])

    def test_qa_flags_non_us_spelling_but_protects_official_names(self):
        flags = meta._meta_qa_flags(
            title="Optimised Services",
            description="Compare colour options from an organisation that prioritises clarity.",
            h1_opt="Organised Service Support",
            input_h1="Service Support",
            forbidden_phrases=[],
        )
        self.assertTrue(
            any(flag.startswith("Non-U.S. English spelling detected:") for flag in flags)
        )

        protected_flags = meta._meta_qa_flags(
            title="Colour Centre",
            description="Explore the official Colour Centre service and its supported options.",
            h1_opt="Colour Centre",
            input_h1="Colour Centre",
            forbidden_phrases=[],
            protected_phrases=["Colour Centre"],
        )
        self.assertFalse(
            any(flag.startswith("Non-U.S. English spelling detected:") for flag in protected_flags)
        )

        valid_us_flags = meta._meta_qa_flags(
            title="Fulfilled Orders",
            description="See how orders are fulfilled by a fulfilling support team.",
            h1_opt="Order Support",
            input_h1="Order Support",
            forbidden_phrases=[],
        )
        self.assertFalse(
            any(flag.startswith("Non-U.S. English spelling detected:") for flag in valid_us_flags)
        )


if __name__ == "__main__":
    unittest.main()
