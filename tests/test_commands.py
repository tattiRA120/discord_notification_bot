import pytest
from unittest.mock import AsyncMock, patch
from database import (
    add_notification_channel,
    remove_notification_channel,
    list_notification_channels,
)


# Test cases for add_notification_channel
@pytest.mark.asyncio
async def test_add_notification_channel_success():
    with patch("database.get_db", return_value=AsyncMock()) as mock_get_db:
        mock_conn = mock_get_db.return_value.__aenter__.return_value

        # Test addition of a channel
        await add_notification_channel(123, "test-channel")

        # Check if executed with correct query
        mock_conn.execute.assert_called_once_with(
            "INSERT OR IGNORE INTO channels (guild_id, channel_name) VALUES (?, ?)",
            (123, "test-channel"),
        )
        mock_conn.commit.assert_called_once()


@pytest.mark.asyncio
async def test_add_notification_channel_name():
    with patch("database.get_db", return_value=AsyncMock()) as mock_get_db:
        mock_conn = mock_get_db.return_value.__aenter__.return_value

        await add_notification_channel(123, "test-channel")

        # Verify call arguments
        mock_conn.execute.assert_called_with(
            "INSERT OR IGNORE INTO channels (guild_id, channel_name) VALUES (?, ?)",
            (123, "test-channel"),
        )


# Test cases for remove_notification_channel
@pytest.mark.asyncio
async def test_remove_notification_channel_success():
    with patch("database.get_db", return_value=AsyncMock()) as mock_get_db:
        mock_conn = mock_get_db.return_value.__aenter__.return_value

        # Test removal of a channel
        await remove_notification_channel(123, "test-channel")

        # Check if executed with correct query
        mock_conn.execute.assert_called_once_with(
            "DELETE FROM channels WHERE guild_id = ? AND channel_name = ?",
            (123, "test-channel"),
        )
        mock_conn.commit.assert_called_once()


# Test cases for list_notification_channels
@pytest.mark.asyncio
async def test_list_notification_channels():
    with patch("database.get_db", return_value=AsyncMock()) as mock_get_db:
        mock_conn = mock_get_db.return_value.__aenter__.return_value
        mock_cursor = AsyncMock()
        mock_conn.execute.return_value = mock_cursor

        # Stub the cursor to return some channels
        mock_cursor.fetchall.return_value = [(123, "channel1"), (123, "channel2")]

        channels = await list_notification_channels(123)

        assert channels == ["channel1", "channel2"]
        mock_conn.execute.assert_called_once_with(
            "SELECT channel_name FROM channels WHERE guild_id = ?", (123,)
        )
