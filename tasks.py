import datetime
from zoneinfo import ZoneInfo
from discord.ext import tasks
import discord # discord モジュールをインポート
import discord.ext.commands as commands
import logging # logging モジュールをインポート

import config # config モジュールをインポート
import constants # constants モジュールをインポート

# ロガーを取得
logger = logging.getLogger(__name__)

# --- タスクを格納する Cog クラス ---
class BotTasks(commands.Cog):
    def __init__(self, bot, bot_commands_cog):
        self.bot = bot
        self.bot_commands_cog = bot_commands_cog
        logger.info("BotTasks Cog initialized.")
        # --- 毎日18時のトリガータスク ---
        # タスクは @tasks.loop デコレータによって定義されます。
        # __init__ でタスクを開始する必要はありません。setup_tasks 関数で行います。

    # --- 月間統計情報送信タスク ---
    # 毎日18:00に実行し、日付が1日かチェック
    @tasks.loop(time=datetime.time(hour=constants.STATS_SEND_HOUR, minute=constants.STATS_SEND_MINUTE, tzinfo=ZoneInfo(constants.TIMEZONE_JST)))
    async def send_monthly_stats_task(self):
        now = datetime.datetime.now(ZoneInfo(constants.TIMEZONE_JST))
        # 実行日が月の1日であるかチェック
        if now.day == constants.DAY_OF_MONTH_FIRST:
            logger.info("Starting monthly stats task.")
            first_day_current = now.replace(day=constants.DAY_OF_MONTH_FIRST)
            prev_month_last_day = first_day_current - datetime.timedelta(days=1) # timedelta(days=1) は定数化しない
            previous_month = prev_month_last_day.strftime("%Y-%m")
            logger.debug(f"Calculating stats for previous month: {previous_month}")

            # 各ギルドに対して前月の統計情報を送信
            for guild in self.bot.guilds:
                guild_id = guild.id
                logger.debug(f"Processing monthly stats for guild {guild_id}")
                channel_id = config.get_notification_channel_id(guild_id)
                if channel_id:
                    channel = self.bot.get_channel(channel_id)
                    if channel:
                        logger.debug(f"Sending monthly stats to channel {channel_id} in guild {guild_id}")
                        embed, month_display = await self.bot_commands_cog._create_monthly_stats_embed(guild, previous_month)
                        if embed:
                            await channel.send(embed=embed)
                            logger.info(f"Monthly stats sent successfully for {month_display} in guild {guild_id}")
                        else:
                            logger.info(f"No monthly stats found for {month_display} in guild {guild_id}")
                            embed = discord.Embed(
                                title=constants.EMBED_TITLE_MONTHLY_STATS,
                                description=f"{month_display}{constants.MESSAGE_NO_CALL_RECORDS}",
                                color=constants.EMBED_COLOR_WARNING
                            )
                            await channel.send(embed=embed)
                    else:
                        logger.warning(f"Notification channel {channel_id} not found for guild {guild_id}")
                else:
                    logger.info(f"No notification channel set for guild {guild_id}")
            logger.info("Monthly stats task finished.")
        else:
            logger.debug("Monthly stats task skipped: not the first day of the month.")


    # --- 年間統計情報送信タスク ---
    # 毎日18:00に実行し、日付が12月31日かチェック
    @tasks.loop(time=datetime.time(hour=constants.STATS_SEND_HOUR, minute=constants.STATS_SEND_MINUTE, tzinfo=ZoneInfo(constants.TIMEZONE_JST)))
    async def send_annual_stats_task(self):
        now = datetime.datetime.now(ZoneInfo(constants.TIMEZONE_JST))
        # 実行日が12月31日であるかチェック
        if now.month == constants.MONTH_OF_YEAR_LAST and now.day == constants.DAY_OF_YEAR_LAST:
            logger.info("Starting annual stats task.")
            year_str = str(now.year)
            logger.debug(f"Calculating stats for year: {year_str}")

            # 各ギルドに対して年間の統計情報を送信
            for guild in self.bot.guilds:
                guild_id = guild.id
                logger.debug(f"Processing annual stats for guild {guild_id}")
                channel_id = config.get_notification_channel_id(guild_id)
                if channel_id:
                    channel = self.bot.get_channel(channel_id)
                    if channel:
                        logger.debug(f"Sending annual stats to channel {channel_id} in guild {guild_id}")
                        embed, year_display = await self.bot_commands_cog._create_annual_stats_embed(guild, year_str)
                        if embed:
                            await channel.send(embed=embed)
                            logger.info(f"Annual stats sent successfully for {year_display} in guild {guild_id}")
                        else:
                            logger.info(f"No annual stats found for {year_display} in guild {guild_id}")
                            embed = discord.Embed(
                                title=constants.EMBED_TITLE_ANNUAL_STATS,
                                description=f"{year_display}{constants.MESSAGE_NO_CALL_RECORDS}",
                                color=constants.EMBED_COLOR_WARNING
                            )
                            await channel.send(embed=embed)
                    else:
                        logger.warning(f"Notification channel {channel_id} not found for guild {guild_id}")
                else:
                    logger.info(f"No notification channel set for guild {guild_id}")
            logger.info("Annual stats task finished.")
        else:
            logger.debug("Annual stats task skipped: not December 31st.")
