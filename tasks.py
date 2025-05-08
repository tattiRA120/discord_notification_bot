import datetime
from zoneinfo import ZoneInfo
from discord.ext import tasks
import discord # discord モジュールをインポート
import discord.ext.commands as commands


from commands import create_monthly_stats_embed, create_annual_stats_embed
import config # config モジュールをインポート

# --- タスクを格納する Cog クラス ---
class BotTasks(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # --- 毎日18時のトリガータスク ---
        # @tasks.loop デコレータが _scheduled_stats メソッドに適用されているため、__init__ で再度設定する必要はありません。

    @tasks.loop(time=datetime.time(hour=18, minute=0, tzinfo=ZoneInfo("Asia/Tokyo")))
    async def _scheduled_stats(self):
        # このメソッドは @tasks.loop デコレータによって自動的にループタスクになります。
        now = datetime.datetime.now(ZoneInfo("Asia/Tokyo"))

        # 前月の統計情報送信（毎月1日）
        if now.day == 1:
            first_day_current = now.replace(day=1)
            prev_month_last_day = first_day_current - datetime.timedelta(days=1)
            previous_month = prev_month_last_day.strftime("%Y-%m")

            # 各ギルドに対して前月の統計情報を送信
            for guild in self.bot.guilds:
                guild_id = guild.id
                channel_id = config.get_notification_channel_id(guild_id)
                if channel_id:
                    channel = self.bot.get_channel(channel_id)
                    if channel:
                        embed, month_display = await create_monthly_stats_embed(guild, previous_month)
                        if embed:
                            await channel.send(embed=embed)
                        else:
                            embed = discord.Embed(
                                title=f"【前月の通話統計】",
                                description=f"{month_display}は通話記録がありませんでした",
                                color=discord.Color.orange()
                            )
                            await channel.send(embed=embed)

        # 年間統計情報送信（毎年12月31日）
        if now.month == 12 and now.day == 31:
            year_str = str(now.year)
            # 各ギルドに対して年間の統計情報を送信
            for guild in self.bot.guilds:
                guild_id = guild.id
                channel_id = config.get_notification_channel_id(guild_id)
                if channel_id:
                    channel = self.bot.get_channel(channel_id)
                    if channel:
                        embed, year_display = await create_annual_stats_embed(guild, year_str)
                        if embed:
                            await channel.send(embed=embed)
                        else:
                            embed = discord.Embed(
                                title=f"【年間の通話統計】",
                                description=f"{year_display}は通話記録がありませんでした",
                                color=discord.Color.orange()
                            )
                            await channel.send(embed=embed)
