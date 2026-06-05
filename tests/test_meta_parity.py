import sys
import types
import unittest


def _install_provider_import_stubs():
    anthropic_stub = types.ModuleType("anthropic")
    anthropic_stub.Anthropic = object
    sys.modules.setdefault("anthropic", anthropic_stub)

    openai_stub = types.ModuleType("openai")
    openai_stub.OpenAI = object
    sys.modules.setdefault("openai", openai_stub)

    google_stub = types.ModuleType("google")
    genai_stub = types.ModuleType("google.genai")
    genai_stub.Client = object
    google_stub.genai = genai_stub
    sys.modules.setdefault("google", google_stub)
    sys.modules.setdefault("google.genai", genai_stub)

    mistralai_stub = types.ModuleType("mistralai")
    mistralai_stub.Mistral = object
    sys.modules.setdefault("mistralai", mistralai_stub)

    groq_stub = types.ModuleType("groq")
    groq_stub.Groq = object
    sys.modules.setdefault("groq", groq_stub)


_install_provider_import_stubs()

from utils import copy_gen


class MetaPromptGuardrailTests(unittest.TestCase):
    def test_prompt_includes_unsupported_claim_guardrail(self):
        prompt = copy_gen._build_prompt(
            copy_gen.DESCRIPTION_PROMPT,
            url="https://example.com/products/widgets",
            keyword="widgets",
            page_type="category",
            brand_name="Example",
            forbidden_phrases="",
            context="Product page context.",
            business_type="ecommerce",
            h1="Widgets",
        )

        self.assertIn("Unsupported claim guardrail", prompt)
        self.assertIn("pricing", prompt)
        self.assertIn("availability", prompt)
        self.assertIn("strategy signals", prompt)

    def test_generate_copy_enforces_title_and_description_lengths(self):
        original_provider = copy_gen.PROVIDERS.get("TestProvider")
        copy_gen.PROVIDERS["TestProvider"] = lambda api_key, **kwargs: {
            "title": "This generated title is intentionally much longer than sixty characters and should be shortened",
            "description": "This generated meta description is intentionally much longer than one hundred fifty five characters so that the normalisation layer trims it safely after the model returns the text.",
            "h1_optimised": "Widgets for Example",
        }

        try:
            result = copy_gen.generate_copy(
                "TestProvider",
                "key",
                url="https://example.com",
                keyword="widgets",
                page_type="category",
                brand_name="Example",
                forbidden_phrases="",
                context="",
                business_type="ecommerce",
                h1="Widgets",
            )
        finally:
            if original_provider is None:
                copy_gen.PROVIDERS.pop("TestProvider", None)
            else:
                copy_gen.PROVIDERS["TestProvider"] = original_provider

        self.assertLessEqual(len(result["title"]), 60)
        self.assertLessEqual(len(result["description"]), 155)
        self.assertEqual(result["h1_optimised"], "Widgets for Example")

    def test_parse_copy_json_strips_fences_and_requires_object(self):
        parsed = copy_gen._parse_copy_json(
            '```json\n{"title":"A","description":"B","h1_optimised":"C","review_notes":"Check claim."}\n```'
        )

        self.assertEqual(parsed["title"], "A")
        self.assertEqual(parsed["description"], "B")
        self.assertEqual(parsed["h1_optimised"], "C")
        self.assertEqual(parsed["review_notes"], "Check claim.")

        with self.assertRaises(ValueError):
            copy_gen._parse_copy_json('["not", "an", "object"]')

    def test_generate_copy_uses_single_structured_provider_call_and_returns_review_notes(self):
        calls = []

        def fake_provider(api_key, **kwargs):
            calls.append(kwargs)
            return {
                "title": "Widget Guide | Example",
                "description": "Compare widgets for practical projects and choose the right option for your needs.",
                "h1_optimised": "Widget Guide",
                "review_notes": "Review pricing claim before publishing.",
            }

        original_provider = copy_gen.PROVIDERS.get("TestProvider")
        copy_gen.PROVIDERS["TestProvider"] = fake_provider
        try:
            result = copy_gen.generate_copy(
                "TestProvider",
                "key",
                url="https://example.com/widgets",
                keyword="widgets",
                page_type="category",
                brand_name="Example",
                forbidden_phrases="",
                context="",
                business_type="ecommerce",
                h1="Widgets",
            )
        finally:
            if original_provider is None:
                copy_gen.PROVIDERS.pop("TestProvider", None)
            else:
                copy_gen.PROVIDERS["TestProvider"] = original_provider

        self.assertEqual(len(calls), 1)
        self.assertEqual(result["review_notes"], "Review pricing claim before publishing.")

    def test_claude_provider_makes_one_structured_prompt_call(self):
        prompts = []

        class FakeMessage:
            content = [types.SimpleNamespace(text='{"title":"A","description":"B","h1_optimised":"C","review_notes":"D"}')]

        class FakeMessages:
            def create(self, **kwargs):
                prompts.append(kwargs["messages"][0]["content"])
                return FakeMessage()

        class FakeAnthropic:
            def __init__(self, api_key):
                self.messages = FakeMessages()

        original_anthropic = copy_gen.anthropic.Anthropic
        copy_gen.anthropic.Anthropic = FakeAnthropic
        try:
            result = copy_gen.generate_copy_claude(
                api_key="key",
                url="https://example.com",
                keyword="widgets",
                page_type="category",
                brand_name="Example",
                forbidden_phrases="",
                context="",
                business_type="ecommerce",
                h1="Widgets",
            )
        finally:
            copy_gen.anthropic.Anthropic = original_anthropic

        self.assertEqual(len(prompts), 1)
        self.assertIn("Return ONLY a raw JSON object", prompts[0])
        self.assertEqual(result["review_notes"], "D")


if __name__ == "__main__":
    unittest.main()
