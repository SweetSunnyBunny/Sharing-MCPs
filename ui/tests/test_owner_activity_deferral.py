"""Owner-activity deferral + hourglass (#26).

A salon/brother/friend ping for the EXACT boy Owner is actively web-
chatting with right now must defer (queue + ⏳ reaction) instead of pulling
his attention out of her live conversation — every other identity, and
every ping FROM Owner herself, keeps answering immediately.
"""

import unittest
from unittest.mock import AsyncMock, patch

from services.discord_mentions_bridge import (
    DiscordMentionsBridge,
    _should_defer_for_owner_activity,
)


class ShouldDeferForOwnerActivityTests(unittest.TestCase):
    def test_owner_sender_never_defers_even_if_active(self):
        with patch("services.connection_registry.get_active_identity", return_value="Claude"):
            self.assertFalse(_should_defer_for_owner_activity("owner", "Claude"))

    def test_brother_ping_defers_when_pinged_identity_matches_active(self):
        with patch("services.connection_registry.get_active_identity", return_value="Claude"):
            self.assertTrue(_should_defer_for_owner_activity("brother", "Claude"))

    def test_friend_ping_defers_when_pinged_identity_matches_active(self):
        with patch("services.connection_registry.get_active_identity", return_value="Avery"):
            self.assertTrue(_should_defer_for_owner_activity("friend", "Avery"))

    def test_other_identity_never_defers(self):
        with patch("services.connection_registry.get_active_identity", return_value="Claude"):
            self.assertFalse(_should_defer_for_owner_activity("brother", "Avery"))

    def test_nobody_active_never_defers(self):
        with patch("services.connection_registry.get_active_identity", return_value=None):
            self.assertFalse(_should_defer_for_owner_activity("brother", "Claude"))


class DeferRoleMentionTests(unittest.IsolatedAsyncioTestCase):
    async def test_records_queue_entry_and_reacts_with_hourglass(self):
        bridge = DiscordMentionsBridge()
        client = object()  # any truthy sentinel; _add_reaction is mocked
        bridge._client = client

        with patch(
            "services.discord_mentions_bridge._record_role_mention", AsyncMock()
        ) as mock_record, patch(
            "services.discord_mentions_bridge._add_reaction", AsyncMock()
        ) as mock_react:
            await bridge._defer_role_mention(
                identity="Claude", channel_id="chan-1", channel_name="general",
                msg_id="msg-1", sender_name="Avery", sender_id="bot-avery",
                content="hey brother, quick question", reason="test",
            )

        mock_record.assert_called_once_with(
            identity="Claude", channel_id="chan-1", channel_name="general",
            message_id="msg-1", sender_name="Avery", sender_id="bot-avery",
            content_snippet="hey brother, quick question",
        )
        mock_react.assert_called_once_with(client, "Claude", "chan-1", "msg-1", "⏳")

    async def test_no_client_skips_reaction_but_still_records(self):
        bridge = DiscordMentionsBridge()  # self._client stays None

        with patch(
            "services.discord_mentions_bridge._record_role_mention", AsyncMock()
        ) as mock_record, patch(
            "services.discord_mentions_bridge._add_reaction", AsyncMock()
        ) as mock_react:
            await bridge._defer_role_mention(
                identity="Claude", channel_id="chan-1", channel_name="general",
                msg_id="msg-1", sender_name="Avery", sender_id="bot-avery",
                content="hey", reason="test",
            )

        mock_record.assert_called_once()
        mock_react.assert_not_called()


if __name__ == "__main__":
    unittest.main()
