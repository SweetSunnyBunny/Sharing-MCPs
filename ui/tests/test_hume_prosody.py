import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from services import hume_prosody


def _make_response(status_code=200, json_data=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data if json_data is not None else {}
    resp.raise_for_status.return_value = None
    return resp


class _FakeAsyncClient:
    """Minimal async-context-manager stand-in for httpx.AsyncClient."""

    def __init__(self, post_side_effect=None, get_side_effects=None):
        self.post = AsyncMock(side_effect=post_side_effect)
        self.get = AsyncMock(side_effect=get_side_effects)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class IsAvailableTests(unittest.TestCase):
    def test_reflects_configured_key(self):
        with patch.object(hume_prosody, "HUME_API_KEY", "key-123"):
            self.assertTrue(hume_prosody.is_available())
        with patch.object(hume_prosody, "HUME_API_KEY", ""):
            self.assertFalse(hume_prosody.is_available())


class SelfGatingTests(unittest.IsolatedAsyncioTestCase):
    async def test_returns_none_immediately_without_key(self):
        with patch.object(hume_prosody, "HUME_API_KEY", ""), patch.object(
            hume_prosody.httpx, "AsyncClient"
        ) as client_cls:
            result = await hume_prosody.analyze_prosody(b"audio-bytes")

        self.assertIsNone(result)
        client_cls.assert_not_called()

    async def test_warns_only_once(self):
        hume_prosody._warned_no_key = False
        try:
            with patch.object(hume_prosody, "HUME_API_KEY", ""), patch.object(
                hume_prosody.log, "info"
            ) as info:
                await hume_prosody.analyze_prosody(b"a")
                await hume_prosody.analyze_prosody(b"b")

            self.assertEqual(info.call_count, 1)
        finally:
            hume_prosody._warned_no_key = False


class ExtractTopEmotionsTests(unittest.TestCase):
    def test_averages_across_segments_and_returns_top_5(self):
        predictions = [{
            "results": {
                "predictions": [{
                    "models": {
                        "prosody": {
                            "grouped_predictions": [{
                                "predictions": [
                                    {"emotions": [
                                        {"name": "Joy", "score": 0.8},
                                        {"name": "Sadness", "score": 0.1},
                                        {"name": "Fear", "score": 0.05},
                                        {"name": "Anger", "score": 0.02},
                                        {"name": "Surprise", "score": 0.01},
                                        {"name": "Boredom", "score": 0.9},
                                    ]},
                                    {"emotions": [
                                        {"name": "Joy", "score": 0.4},
                                        {"name": "Sadness", "score": 0.3},
                                        {"name": "Fear", "score": 0.05},
                                        {"name": "Anger", "score": 0.02},
                                        {"name": "Surprise", "score": 0.01},
                                        {"name": "Boredom", "score": 0.1},
                                    ]},
                                ],
                            }],
                        },
                    },
                }],
            },
        }]

        result = hume_prosody._extract_top_emotions(predictions)

        self.assertIsNotNone(result)
        self.assertEqual(len(result), 5)  # only 5 of the 6 seen emotions survive
        # Joy averages (0.8+0.4)/2=0.6, Boredom averages (0.9+0.1)/2=0.5 — top 2.
        self.assertEqual(result[0]["emotion"], "Joy")
        self.assertAlmostEqual(result[0]["score"], 0.6)
        self.assertEqual(result[1]["emotion"], "Boredom")
        self.assertAlmostEqual(result[1]["score"], 0.5)
        # Surprise (avg 0.01) is the lowest of the 6 and gets dropped by top-5.
        self.assertNotIn("Surprise", [e["emotion"] for e in result])

    def test_returns_none_on_missing_shape(self):
        self.assertIsNone(hume_prosody._extract_top_emotions([{"results": {}}]))
        self.assertIsNone(hume_prosody._extract_top_emotions([]))
        self.assertIsNone(hume_prosody._extract_top_emotions(None))


class AnalyzeProsodyFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_full_flow_submits_polls_and_extracts(self):
        submit_response = _make_response(json_data={"job_id": "job-1"})
        status_response = _make_response(json_data={"state": {"status": "COMPLETED"}})
        predictions_payload = [{
            "results": {"predictions": [{"models": {"prosody": {
                "grouped_predictions": [{"predictions": [
                    {"emotions": [{"name": "Joy", "score": 0.9}]},
                ]}],
            }}}]},
        }]
        predictions_response = _make_response(json_data=predictions_payload)

        fake_client = _FakeAsyncClient(
            post_side_effect=[submit_response],
            get_side_effects=[status_response, predictions_response],
        )

        with patch.object(hume_prosody, "HUME_API_KEY", "key"), patch.object(
            hume_prosody.httpx, "AsyncClient", return_value=fake_client
        ), patch.object(hume_prosody.asyncio, "sleep", new=AsyncMock()):
            result = await hume_prosody.analyze_prosody(b"audio", "audio/webm")

        self.assertEqual(result, [{"emotion": "Joy", "score": 0.9}])

    async def test_never_raises_on_network_error(self):
        fake_client = _FakeAsyncClient(post_side_effect=RuntimeError("network down"))

        with patch.object(hume_prosody, "HUME_API_KEY", "key"), patch.object(
            hume_prosody.httpx, "AsyncClient", return_value=fake_client
        ):
            result = await hume_prosody.analyze_prosody(b"audio")

        self.assertIsNone(result)

    async def test_returns_none_when_job_fails(self):
        submit_response = _make_response(json_data={"job_id": "job-1"})
        status_response = _make_response(json_data={"state": {"status": "FAILED"}})

        fake_client = _FakeAsyncClient(
            post_side_effect=[submit_response],
            get_side_effects=[status_response],
        )

        with patch.object(hume_prosody, "HUME_API_KEY", "key"), patch.object(
            hume_prosody.httpx, "AsyncClient", return_value=fake_client
        ), patch.object(hume_prosody.asyncio, "sleep", new=AsyncMock()):
            result = await hume_prosody.analyze_prosody(b"audio")

        self.assertIsNone(result)

    async def test_returns_none_when_submit_has_no_job_id(self):
        submit_response = _make_response(json_data={})
        fake_client = _FakeAsyncClient(post_side_effect=[submit_response])

        with patch.object(hume_prosody, "HUME_API_KEY", "key"), patch.object(
            hume_prosody.httpx, "AsyncClient", return_value=fake_client
        ):
            result = await hume_prosody.analyze_prosody(b"audio")

        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
