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
    def test_openai_fallback_uses_current_gpt_5_model(self):
        captured = {}

        class FakeCompletions:
            def create(self, **kwargs):
                captured.update(kwargs)
                return types.SimpleNamespace(
                    choices=[
                        types.SimpleNamespace(
                            message=types.SimpleNamespace(
                                content='{"title":"A","description":"B","h1_optimised":"C"}'
                            )
                        )
                    ]
                )

        class FakeClient:
            def __init__(self, api_key):
                self.chat = types.SimpleNamespace(completions=FakeCompletions())

        original_openai = copy_gen.openai.OpenAI
        copy_gen.openai.OpenAI = FakeClient
        try:
            copy_gen.generate_copy_openai(
                api_key="key",
                url="https://example.com",
                keyword="widgets",
            )
        finally:
            copy_gen.openai.OpenAI = original_openai

        self.assertEqual(captured["model"], "gpt-5.5")
        self.assertEqual(captured["max_completion_tokens"], 512)
        self.assertNotIn("max_tokens", captured)

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

    def test_ecommerce_cta_examples_do_not_suggest_unverified_shipping_claims(self):
        prompt = copy_gen._build_prompt(
            copy_gen.COPY_PROMPT,
            url="https://example.com/products/widgets",
            keyword="widgets",
            page_type="category",
            brand_name="Example",
            forbidden_phrases="",
            context="Product page context.",
            business_type="ecommerce",
            h1="Widgets",
        )

        self.assertIn("Shop now, Browse the collection, Order today", prompt)
        self.assertNotIn("Free shipping", prompt)

    def test_prompt_uses_relaxed_length_guidance(self):
        prompt = copy_gen._build_prompt(
            copy_gen.COPY_PROMPT,
            url="https://example.com/products/widgets",
            keyword="widgets",
            page_type="category",
            brand_name="Example",
            forbidden_phrases="",
            context="Product page context.",
            business_type="ecommerce",
            h1="Widgets",
        )

        self.assertIn("aim for up to 90 characters", prompt)
        self.assertIn("aim for up to 200 characters", prompt)
        self.assertIn("H1 has no hard character limit but should aim for under 80 characters", prompt)
        self.assertNotIn("Count carefully. This is a strict limit.", prompt)

    def test_prompt_includes_secondary_keyword_as_optional_signal(self):
        prompt = copy_gen._build_prompt(
            copy_gen.COPY_PROMPT,
            url="https://example.com/products/widgets",
            keyword="widgets",
            page_type="category",
            brand_name="Example",
            forbidden_phrases="",
            context="Product page context.",
            business_type="ecommerce",
            h1="Widgets",
            runner_up_keyword="blue widgets",
        )

        self.assertIn("Secondary keyword variant: blue widgets", prompt)
        self.assertIn("Use the secondary keyword only if it fits naturally", prompt)
        self.assertIn("Do not force it", prompt)

    def test_prompt_uses_structured_brand_context_block(self):
        prompt = copy_gen._build_prompt(
            copy_gen.COPY_PROMPT,
            url="https://example.com/products/widgets",
            keyword="widgets",
            page_type="category",
            brand_name="Example",
            forbidden_phrases="",
            context="Product page context.",
            brand_context=(
                "BRAND CONTEXT:\n"
                "- Voice: Plainspoken expert\n"
                "- Tone: Confident\n"
                "- Target audience: Facilities managers"
            ),
            business_type="ecommerce",
            h1="Widgets",
        )

        self.assertIn("BRAND CONTEXT:", prompt)
        self.assertIn("- Voice: Plainspoken expert", prompt)
        self.assertIn("- Tone: Confident", prompt)
        self.assertIn("- Target audience: Facilities managers", prompt)
        self.assertIn("Additional context: Product page context.", prompt)

    def test_generate_copy_preserves_long_copy_and_flags_soft_length_review(self):
        long_title = "This generated title is intentionally much longer than seventy characters so it keeps the brand CTA intact | Example"
        long_description = "This generated meta description is intentionally much longer than one hundred eighty characters so the normalisation layer must preserve the CTA, the differentiator, and the brand mention instead of silently cutting the ending."
        original_provider = copy_gen.PROVIDERS.get("TestProvider")
        copy_gen.PROVIDERS["TestProvider"] = lambda api_key, **kwargs: {
            "title": long_title,
            "description": long_description,
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
                runner_up_keyword="blue widgets",
            )
        finally:
            if original_provider is None:
                copy_gen.PROVIDERS.pop("TestProvider", None)
            else:
                copy_gen.PROVIDERS["TestProvider"] = original_provider

        self.assertEqual(result["title"], long_title)
        self.assertEqual(result["description"], long_description)
        self.assertEqual(result["h1_optimised"], "Widgets for Example")
        self.assertIn("Title is over 90 characters; review before publishing.", result["review_notes"])
        self.assertIn("Description is over 200 characters; review before publishing.", result["review_notes"])

    def test_length_review_notes_append_to_existing_notes(self):
        original_provider = copy_gen.PROVIDERS.get("TestProvider")
        copy_gen.PROVIDERS["TestProvider"] = lambda api_key, **kwargs: {
            "title": "Short title",
            "description": "This generated meta description is intentionally much longer than one hundred eighty characters so the existing review note remains visible and the long-description warning is added without trimming useful copy.",
            "h1_optimised": "Widgets for Example",
            "review_notes": "Review pricing claim before publishing.",
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

        self.assertIn("Review pricing claim before publishing.", result["review_notes"])
        self.assertIn("Description is over 200 characters; review before publishing.", result["review_notes"])
        self.assertNotIn("Title is over 90 characters", result["review_notes"])

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
                runner_up_keyword="blue widgets",
            )
        finally:
            if original_provider is None:
                copy_gen.PROVIDERS.pop("TestProvider", None)
            else:
                copy_gen.PROVIDERS["TestProvider"] = original_provider

        self.assertEqual(len(calls), 1)
        self.assertEqual(result["review_notes"], "Review pricing claim before publishing.")
        self.assertEqual(calls[0]["runner_up_keyword"], "blue widgets")

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
