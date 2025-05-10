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

    async def _send_stats_to_channel(self, guild, period_display, create_embed_func, embed_title):
        """
        共通の統計情報送信ロジック

        Args:
            guild (discord.Guild): 統計情報を送信するギルドオブジェクト
            period_display (str): 統計情報の表示期間 (例: "2023年10月", "2023年")
            create_embed_func (callable): 統計情報Embedを作成する非同期関数
            embed_title (str): Embedのタイトル
        """
        guild_id = guild.id
        logger.debug(f"Processing stats for guild {guild_id} for period {period_display}")
        channel_id = config.get_notification_channel_id(guild_id)
        if channel_id:
            channel = self.bot.get_channel(channel_id)
            if channel:
                logger.debug(f"Sending stats to channel {channel_id} in guild {guild_id}")
                embed, display_period = await create_embed_func(guild, period_display)
                if embed:
                    await channel.send(embed=embed)
                    logger.info(f"Stats sent successfully for {display_period} in guild {guild_id}")
                else:
                    logger.info(f"No stats found for {display_period} in guild {guild_id}")
                    embed = discord.Embed(
                        title=embed_title,
                        description=f"{display_period}{constants.MESSAGE_NO_CALL_RECORDS}",
                        color=constants.EMBED_COLOR_WARNING
                    )
                    await channel.send(embed=embed)
            else:
                logger.warning(f"Notification channel {channel_id} not found for guild {guild_id}")
        else:
            logger.info(f"No notification channel set for guild {guild_id}")


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
                await self._send_stats_to_channel(
                    guild,
                    previous_month,
                    self.bot_commands_cog._create_monthly_stats_embed,
                    constants.EMBED_TITLE_MONTHLY_STATS
                )
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
                await self._send_stats_to_channel(
                    guild,
                    year_str,
                    self.bot_commands_cog._create_annual_stats_embed,
                    constants.EMBED_TITLE_ANNUAL_STATS
                )
            logger.info("Annual stats task finished.")
        else:
            logger.debug("Annual stats task skipped: not December 31st.")
