import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from api.settings import _validate_provider_config
from services.codex_cli import (
    get_codex_default_model_id,
    get_codex_model_ids,
    normalize_codex_model,
    validate_codex_model,
)


class CodexModelValidationTests(unittest.TestCase):
    def _cache(self, root: str) -> Path:
        cache = Path(root) / "models_cache.json"
        cache.write_text(
            json.dumps(
                {
                    "models": [
                        {
                            "slug": "gpt-5.6-sol",
                            "visibility": "list",
                            "use_responses_lite": True,
                        },
                        {
                            "slug": "gpt-5.6-terra",
                            "visibility": "list",
                            "use_responses_lite": True,
                        },
                        {"slug": "gpt-5.5", "visibility": "list"},
                        {"slug": "codex-auto-review", "visibility": "hide"},
                    ]
                }
            ),
            encoding="utf-8",
        )
        return cache

    def test_model_cache_keeps_chatgpt_models_and_known_sol_route(self):
        with TemporaryDirectory() as tmpdir:
            models = get_codex_model_ids(self._cache(tmpdir))

        self.assertIn("gpt-5.5", models)
        self.assertIn("gpt-5.6-sol", models)
        self.assertNotIn("gpt-5.6", models)
        self.assertNotIn("codex-auto-review", models)

    def test_model_normalization_expands_bare_version(self):
        with TemporaryDirectory() as tmpdir:
            models = get_codex_model_ids(self._cache(tmpdir))
            with patch("services.codex_cli.get_codex_model_ids", return_value=models):
                self.assertEqual(normalize_codex_model("5.6"), "gpt-5.6-sol")

    def test_astra_is_accepted_when_the_model_cache_omits_it(self):
        with TemporaryDirectory() as tmpdir:
            models = get_codex_model_ids(self._cache(tmpdir))
            self.assertIn("gpt-6-astra", models)
            with patch("services.codex_cli.get_codex_model_ids", return_value=models):
                for requested in ("gpt-6-astra", "6-astra", "gpt-6", "6"):
                    with self.subTest(model=requested):
                        self.assertEqual(normalize_codex_model(requested), "gpt-6-astra")
                        self.assertIsNone(validate_codex_model(requested))
                        self.assertIsNone(
                            _validate_provider_config("codex", {"model": requested})
                        )

    def test_default_model_uses_lowest_visible_chatgpt_priority(self):
        with TemporaryDirectory() as tmpdir:
            default = get_codex_default_model_id(self._cache(tmpdir))

        self.assertEqual(default, "gpt-5.5")

    def test_unsuffixed_newer_version_uses_sol_route(self):
        with TemporaryDirectory() as tmpdir:
            cache = self._cache(tmpdir)
            models = get_codex_model_ids(cache)
            with patch("services.codex_cli.get_codex_model_ids", return_value=models):
                result = validate_codex_model("5.6")

        self.assertIsNone(result)

    def test_known_and_blank_models_are_accepted(self):
        with patch("services.codex_cli.get_codex_model_ids", return_value=["gpt-5.5"]):
            self.assertIsNone(validate_codex_model("gpt-5.5"))
            self.assertIsNone(validate_codex_model(""))
            self.assertIsNone(_validate_provider_config("codex", {"model": "gpt-5.5"}))

    def test_codex_runtime_must_be_app_server_or_exec(self):
        self.assertEqual(
            _validate_provider_config("codex", {"runtime": "something-else"}),
            "Codex runtime must be app-server or exec.",
        )

    def test_missing_cache_does_not_block_future_models(self):
        with patch("services.codex_cli.get_codex_model_ids", return_value=[]):
            self.assertIsNone(validate_codex_model("gpt-future"))


if __name__ == "__main__":
    unittest.main()
