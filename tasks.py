import datetime
from zoneinfo import ZoneInfo
from discord.ext import tasks

import utils
from commands import create_monthly_stats_embed, create_annual_stats_embed

# --- 毎日18時のトリガータスク ---
@tasks.loop(time=datetime.time(hour=18, minute=0, tzinfo=ZoneInfo("Asia/Tokyo")))
async def scheduled_stats():
    now = datetime.datetime.now(ZoneInfo("Asia/Tokyo"))

    # 前月の統計情報送信（毎月1日）
    if now.day == 1:
        first_day_current = now.replace(day=1)
        prev_month_last_day = first_day_current - datetime.timedelta(days=1)
        previous_month = prev_month_last_day.strftime("%Y-%m")

        for guild_id, channel_id in utils.server_notification_channels.items():
            guild = utils.bot.get_guild(int(guild_id))
            channel = utils.bot.get_channel(channel_id)
            if guild and channel:
                embed, month_display = await create_monthly_stats_embed(guild, previous_month)
                if embed:
                    await channel.send(embed=embed)
                else:
                    embed = utils.discord.Embed(
                        title=f"【前月の通話統計】",
                        description=f"{month_display}は通話記録がありませんでした",
                        color=utils.discord.Color.orange()
                    )
                    await channel.send(embed=embed)

    # 年間統計情報送信（毎年12月31日）
    if now.month == 12 and now.day == 31:
        year_str = str(now.year)
        for guild_id, channel_id in utils.server_notification_channels.items():
            guild = utils.bot.get_guild(int(guild_id))
            channel = utils.bot.get_channel(channel_id)
            if guild and channel:
                embed, year_display = await create_annual_stats_embed(guild, year_str)
                if embed:
                    await channel.send(embed=embed)
                else:
                    embed = utils.discord.Embed(
                        title=f"【年間の通話統計】",
                        description=f"{month_display}は通話記録がありませんでした",
                        color=utils.discord.Color.orange()
                    )
                    await channel.send(embed=embed)
