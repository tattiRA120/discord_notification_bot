import logging
from unittest.mock import MagicMock

from main import DiscordHandler


def test_discord_handler_ignores_reconnect_log():
    mock_bot = MagicMock()
    handler = DiscordHandler(mock_bot)

    record = logging.LogRecord(
        name="discord.client",
        level=logging.ERROR,
        pathname="client.py",
        lineno=781,
        msg="Attempting a reconnect in %.2fs",
        args=(0.89,),
        exc_info=None,
    )

    # emitを実行しても sent_messages や buffer に追加されず、送信も呼ばれないこと
    handler.emit(record)
    assert len(handler.sent_messages) == 0
    assert len(handler.buffer) == 0


def test_discord_handler_ignores_bot_is_ready():
    mock_bot = MagicMock()
    handler = DiscordHandler(mock_bot)

    record = logging.LogRecord(
        name="root",
        level=logging.WARNING,
        pathname="main.py",
        lineno=100,
        msg="Bot is ready.",
        args=(),
        exc_info=None,
    )

    handler.emit(record)
    assert len(handler.sent_messages) == 0


def test_discord_handler_ignores_internal_logger():
    mock_bot = MagicMock()
    handler = DiscordHandler(mock_bot)

    record = logging.LogRecord(
        name="bot.internal",
        level=logging.ERROR,
        pathname="main.py",
        lineno=100,
        msg="Internal error",
        args=(),
        exc_info=None,
    )

    handler.emit(record)
    assert len(handler.sent_messages) == 0


def test_discord_handler_ignores_info_level():
    mock_bot = MagicMock()
    handler = DiscordHandler(mock_bot)

    record = logging.LogRecord(
        name="root",
        level=logging.INFO,
        pathname="main.py",
        lineno=100,
        msg="Some info log",
        args=(),
        exc_info=None,
    )

    handler.emit(record)
    assert len(handler.sent_messages) == 0


def test_discord_handler_processes_normal_error():
    mock_bot = MagicMock()
    mock_bot.loop = None
    handler = DiscordHandler(mock_bot)
    handler.webhook_url = "https://discord.com/api/webhooks/dummy"

    record = logging.LogRecord(
        name="root",
        level=logging.ERROR,
        pathname="main.py",
        lineno=100,
        msg="Something went wrong!",
        args=(),
        exc_info=None,
    )

    handler.emit(record)
    # sent_messages と buffer に追加されること
    assert "Something went wrong!" in handler.sent_messages
    assert len(handler.buffer) == 1
