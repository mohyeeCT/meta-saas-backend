"""
Structural tests for model selection support in meta-saas-backend.

These tests verify signatures and field declarations without making real API
calls or importing provider libraries (anthropic, openai, etc.).
"""
import inspect
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock


def _stub_provider_libs():
    """Pre-stub all external provider libraries so copy_gen can be imported."""
    for mod_name in [
        "anthropic",
        "openai",
        "mistralai",
        "groq",
        "google",
        "google.generativeai",
    ]:
        sys.modules.setdefault(mod_name, MagicMock())

    # google.genai is accessed as `from google import genai as google_genai`
    google_mock = sys.modules.get("google", MagicMock())
    google_mock.genai = MagicMock()
    sys.modules["google"] = google_mock
    sys.modules["google.genai"] = google_mock.genai


_stub_provider_libs()

# Add repo root to path so imports work when run from the tests/ directory
sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.copy_gen import (  # noqa: E402
    generate_copy_claude,
    generate_copy_openai,
    generate_copy_gemini,
    generate_copy_mistral,
    generate_copy_groq,
)


class ModelParameterInSignatureTests(unittest.TestCase):
    """Each provider function must expose a 'model' keyword argument."""

    def _assert_model_param(self, fn):
        sig = inspect.signature(fn)
        self.assertIn(
            "model",
            sig.parameters,
            f"{fn.__name__} must accept a 'model' parameter",
        )
        default = sig.parameters["model"].default
        self.assertEqual(
            default,
            "",
            f"{fn.__name__} 'model' must default to empty string so existing callers are unaffected",
        )

    def test_claude_has_model_param(self):
        self._assert_model_param(generate_copy_claude)

    def test_openai_has_model_param(self):
        self._assert_model_param(generate_copy_openai)

    def test_gemini_has_model_param(self):
        self._assert_model_param(generate_copy_gemini)

    def test_mistral_has_model_param(self):
        self._assert_model_param(generate_copy_mistral)

    def test_groq_has_model_param(self):
        self._assert_model_param(generate_copy_groq)


class MetaSettingsModelFieldTests(unittest.TestCase):
    """MetaSettings must declare a 'model' field with an empty-string default."""

    def test_metasettings_has_model_field(self):
        source = Path("routers/meta.py").read_text(encoding="utf-8")
        self.assertIn(
            'model: str = ""',
            source,
            "MetaSettings must declare model: str = \"\"",
        )

    def test_generate_copy_call_passes_model(self):
        source = Path("routers/meta.py").read_text(encoding="utf-8")
        self.assertIn(
            'model=settings.get("model", "")',
            source,
            "generate_copy call in meta.py must pass model from settings",
        )


if __name__ == "__main__":
    unittest.main()
