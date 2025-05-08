import discord
from discord.ext import commands
import os
from dotenv import load_dotenv
from zoneinfo import ZoneInfo
import datetime
import config
import formatters

from database import get_total_call_time, update_member_monthly_stats, get_guild_settings
from config import get_notification_channel_id # get_notification_channel_id をインポート
# config, voice_state_manager, formatters は後でインポートします

# .envファイルの環境変数を読み込む
load_dotenv()


# --- 10時間達成通知用ヘルパー関数 ---
async def check_and_notify_milestone(bot, member: discord.Member, guild: discord.Guild, before_total: float, after_total: float):
    # config モジュールから get_notification_channel_id をインポートする必要があります
    guild_id = str(guild.id)
    notification_channel_id = get_notification_channel_id(guild.id) # config から取得

    if notification_channel_id is None:
        return # 通知先チャンネルが設定されていない場合は何もしない

    notification_channel = bot.get_channel(notification_channel_id)
    if not notification_channel:
        print(f"通知チャンネルが見つかりません: ギルドID {guild_id}, チャンネルID {notification_channel_id}")
        return

    hour_threshold = 10 * 3600 # 10時間 = 36000秒
    before_milestone = int(before_total // hour_threshold)
    after_milestone = int(after_total // hour_threshold)

    if after_milestone > before_milestone:
        achieved_hours = after_milestone * 10
        embed = discord.Embed(
            title="🎉 通話時間達成！ 🎉",
            description=f"{member.mention} さんの累計通話時間が **{achieved_hours}時間** を達成しました！",
            color=discord.Color.gold()
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="メンバー", value=member.display_name, inline=True)
        embed.add_field(name="達成時間", value=f"{achieved_hours} 時間", inline=True)
        embed.add_field(name="現在の総累計時間", value=formatters.format_duration(after_total), inline=False) # Use imported formatters
        embed.timestamp = datetime.datetime.now(ZoneInfo("Asia/Tokyo"))

        try:
            await notification_channel.send(embed=embed)
        except discord.Forbidden:
            print(f"エラー: チャンネル {notification_channel.name} ({notification_channel_id}) への送信権限がありません。")
        except Exception as e:
            print(f"通知送信中にエラーが発生しました: {e}")
