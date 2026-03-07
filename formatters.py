import datetime
from zoneinfo import ZoneInfo
import constants
import discord
import logging
import traceback


def format_duration(duration_seconds):
    """秒数を時間:分:秒の形式にフォーマットする"""
    seconds = int(duration_seconds)
    hours, remainder = divmod(seconds, constants.SECONDS_PER_HOUR)
    minutes, seconds = divmod(remainder, constants.SECONDS_PER_MINUTE)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def convert_utc_to_jst(utc_time):
    """UTC時刻をJSTに変換する"""
    return utc_time.astimezone(ZoneInfo(constants.TIMEZONE_JST))


def create_log_embed(record: logging.LogRecord):
    """ログレコードからDiscord埋め込みを作成する"""
    if record.levelno >= logging.ERROR:
        color = constants.EMBED_COLOR_ERROR
        title = "🚨 エラー発生！ 🚨"
    elif record.levelno >= logging.WARNING:
        color = constants.EMBED_COLOR_WARNING
        title = "⚠️ 警告！ ⚠️"
    else:
        color = constants.EMBED_COLOR_INFO
        title = "ℹ️ 情報 ℹ️"

    embed = discord.Embed(
        title=title,
        description=f"```\n{record.getMessage()}\n```",
        color=color,
        timestamp=datetime.datetime.fromtimestamp(
            record.created, tz=ZoneInfo(constants.TIMEZONE_JST)
        ),
    )

    embed.add_field(name="レベル", value=record.levelname, inline=True)
    embed.add_field(
        name="ファイル:行", value=f"{record.filename}:{record.lineno}", inline=True
    )
    embed.add_field(name="関数", value=record.funcName, inline=True)

    if record.exc_info:
        # exc_info が存在する場合、トレースバック情報を追加
        exc_type, exc_value, exc_traceback = record.exc_info
        tb_string = "".join(
            traceback.format_exception(exc_type, exc_value, exc_traceback)
        )
        embed.add_field(
            name="トレースバック",
            value=f"```python\n{tb_string[:1000]}...\n```",
            inline=False,
        )  # Discordの文字数制限を考慮

    return embed
