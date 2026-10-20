import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from services import scribe


class ScribeProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_dispatches_claude_code_provider(self):
        with patch.object(
            scribe, "_generate_with_claude_code", new=AsyncMock(return_value="digest")
        ) as generate:
            result = await scribe._generate_digest_text(
                "claude-code", "claude-haiku-4-5", "prompt"
            )
        self.assertEqual(result, "digest")
        generate.assert_awaited_once_with("claude-haiku-4-5", "prompt")

    async def test_openrouter_generation_uses_saved_credentials_without_exposing_them(self):
        response = MagicMock()
        response.choices = [MagicMock(message=MagicMock(content="  warm digest  "))]
        create = AsyncMock(return_value=response)
        client = MagicMock()
        client.chat.completions.create = create

        with patch.object(
            scribe, "_get_openrouter_credentials", new=AsyncMock(
                return_value=("secret-key", "https://openrouter.ai/api/v1")
            )
        ), patch("openai.AsyncOpenAI", return_value=client):
            result = await scribe._generate_with_openrouter(
                "anthropic/claude-haiku-4.5", "prompt"
            )

        self.assertEqual(result, "warm digest")
        create.assert_awaited_once()
