import discord
from discord import app_commands
from discord.ext import commands, tasks
import datetime
import os
import json
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
import asyncio

# .envファイルの環境変数を読み込む
load_dotenv()
TOKEN = os.getenv('DISCORD_BOT_TOKEN')

intents = discord.Intents.default()
intents.voice_states = True
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# -----------------------------
# 通知先チャンネル設定
server_notification_channels = {}
CHANNELS_FILE = "channels.json"

def save_channels_to_file():
    with open(CHANNELS_FILE, "w") as f:
        json.dump(server_notification_channels, f)

def load_channels_from_file():
    global server_notification_channels
    if os.path.exists(CHANNELS_FILE):
        with open(CHANNELS_FILE, "r") as f:
            server_notification_channels = json.load(f)
    # キーを文字列に統一
    server_notification_channels = {str(guild_id): channel_id for guild_id, channel_id in server_notification_channels.items()}

# -----------------------------
# 通話統計用変数と関数
ongoing_voice_sessions = {}  # {guild_id: {channel_id: {"start_time": ..., "participants": [...]}}}
server_monthly_stats = {}    # {guild_id: { "YYYY-MM": { "total_multi_time": 秒数, "sessions": [...], "member_times": {member_id: 秒数} } } }
STATS_FILE = "voice_stats.json"

def load_stats():
    global server_monthly_stats
    if os.path.exists(STATS_FILE):
        with open(STATS_FILE, "r") as f:
            server_monthly_stats = json.load(f)
    else:
        server_monthly_stats = {}

def save_stats():
    with open(STATS_FILE, "w") as f:
        json.dump(server_monthly_stats, f, default=str)

def convert_utc_to_jst(utc_time):
    return utc_time.astimezone(ZoneInfo("Asia/Tokyo"))

def get_month_key(dt: datetime.datetime):
    jst = convert_utc_to_jst(dt)
    return jst.strftime("%Y-%m")

def update_stats(guild_id, channel, session_start, session_end, participants):
    duration = (session_end - session_start).total_seconds()
    month_key = get_month_key(session_start)
    guild_key = str(guild_id)
    if guild_key not in server_monthly_stats:
        server_monthly_stats[guild_key] = {}
    if month_key not in server_monthly_stats[guild_key]:
        server_monthly_stats[guild_key][month_key] = {
            "total_multi_time": 0,
            "sessions": [],
            "member_times": {}
        }
    data = server_monthly_stats[guild_key][month_key]
    data["total_multi_time"] += duration
    data["sessions"].append({
        "duration": duration,
        "start": session_start.isoformat()
    })
    for member_id in participants:
        data["member_times"][str(member_id)] = data["member_times"].get(str(member_id), 0) + duration
    save_stats()

# -----------------------------
# 通話開始通知用（既存） call_sessions
call_sessions = {}  # {guild_id: {voice_channel_id: {"start_time": ..., "first_member": ...}}}

# -----------------------------
# イベント処理：on_voice_state_update
@bot.event
async def on_voice_state_update(member, before, after):
    guild_id = member.guild.id

    # 対象のチャンネル（入室 or 退室）
    channel = after.channel if after.channel else before.channel
    if channel is None:
        return
    channel_id = channel.id
    current_count = len(channel.members)

    # -----------------------------
    # 【統計】2人以上になったタイミングでセッション開始、2人以下になったらセッション終了
    if guild_id not in ongoing_voice_sessions:
        ongoing_voice_sessions[guild_id] = {}

    # 2人以上の場合：セッション開始（既に開始済みなら何もしない）
    if current_count >= 2 and channel_id not in ongoing_voice_sessions[guild_id]:
        start_time = datetime.datetime.now(datetime.timezone.utc)
        ongoing_voice_sessions[guild_id][channel_id] = {
            "start_time": start_time,
            "participants": [m.id for m in channel.members]
        }
    # 2人以下の場合：セッション終了（記録があれば統計更新）
    elif current_count < 2 and channel_id in ongoing_voice_sessions[guild_id]:
        session = ongoing_voice_sessions[guild_id].pop(channel_id)
        session_start = session["start_time"]
        session_end = datetime.datetime.now(datetime.timezone.utc)
        participants = session["participants"]
        update_stats(guild_id, channel, session_start, session_end, participants)

    # -----------------------------
    # 【通知】通話開始／終了の通知処理
    # 通話開始：誰もいなかったチャンネルに入室した場合
    if before.channel is None and after.channel is not None:
        if guild_id not in call_sessions:
            call_sessions[guild_id] = {}
        if channel_id not in call_sessions[guild_id]:
            start_time = datetime.datetime.now(datetime.timezone.utc)
            call_sessions[guild_id][channel_id] = {"start_time": start_time, "first_member": member.id}
            jst_time = convert_utc_to_jst(start_time)
            embed = discord.Embed(title="通話開始", color=0xea958f)
            embed.set_thumbnail(url=f"{member.avatar.url}?size=128")
            embed.add_field(name="`チャンネル`", value=f"{after.channel.name}")
            embed.add_field(name="`始めた人`", value=f"{member.display_name}")
            embed.add_field(name="`開始時間`", value=f"{jst_time.strftime('%Y/%m/%d %H:%M:%S')}")
            if str(guild_id) in server_notification_channels:
                notification_channel = bot.get_channel(server_notification_channels[str(guild_id)])
                if notification_channel:
                    await notification_channel.send(
                        content="@everyone",
                        embed=embed,
                        allowed_mentions=discord.AllowedMentions(everyone=True)
                    )
    # 通話終了：退室して通話チャンネルが空になった場合
    elif before.channel is not None and after.channel is None:
        if guild_id in call_sessions and channel_id in call_sessions[guild_id]:
            voice_channel = before.channel
            if len(voice_channel.members) == 0:
                session = call_sessions[guild_id].pop(channel_id)
                start_time = session["start_time"]
                call_duration = datetime.datetime.now(datetime.timezone.utc) - start_time
                hours, remainder = divmod(call_duration.total_seconds(), 3600)
                minutes, seconds = divmod(remainder, 60)
                duration_str = f"{int(hours):02}:{int(minutes):02}:{int(seconds):02}"
                embed = discord.Embed(title="通話終了", color=0x938dfd)
                embed.add_field(name="`チャンネル`", value=f"{voice_channel.name}")
                embed.add_field(name="`通話時間`", value=f"{duration_str}")
                if str(guild_id) in server_notification_channels:
                    notification_channel = bot.get_channel(server_notification_channels[str(guild_id)])
                    if notification_channel:
                        await notification_channel.send(embed=embed)

# -----------------------------
# 通知先チャンネル変更のスラッシュコマンド
@bot.tree.command(name="changesendchannel", description="通知先のチャンネルを変更します")
@app_commands.describe(channel="通知を送信するチャンネル")
async def changesendchannel(interaction: discord.Interaction, channel: discord.TextChannel):
    guild_id = str(interaction.guild.id)
    if guild_id in server_notification_channels and server_notification_channels[guild_id] == channel.id:
        current_channel = bot.get_channel(server_notification_channels[guild_id])
        await interaction.response.send_message(f"すでに {current_channel.mention} で設定済みです。")
    else:
        server_notification_channels[guild_id] = channel.id
        save_channels_to_file()
        await interaction.response.send_message(f"通知先のチャンネルが {channel.mention} に設定されました。")

# -----------------------------
# 通話統計表示のスラッシュコマンド
@bot.tree.command(name="voicestats", description="通話統計を表示します")
@app_commands.describe(month="統計の対象となる月（例: 2025-03）。省略時は現在の月")
async def voicestats(interaction: discord.Interaction, month: str = None):
    # monthが指定されていなければ現在の月（JST）を利用
    if month is None:
        now = datetime.datetime.now(datetime.timezone.utc)
        month = convert_utc_to_jst(now).strftime("%Y-%m")
    guild_key = str(interaction.guild.id)
    if guild_key not in server_monthly_stats or month not in server_monthly_stats[guild_key]:
        await interaction.response.send_message(f"{month} の統計情報は存在しません。", ephemeral=True)
        return

    stats = server_monthly_stats[guild_key][month]
    total_seconds = stats.get("total_multi_time", 0)
    total_time = str(datetime.timedelta(seconds=int(total_seconds)))
    session_count = len(stats.get("sessions", []))
    
    embed = discord.Embed(title=f"通話統計 ({month})", color=0x00ff00)
    embed.add_field(name="総通話時間", value=total_time, inline=False)
    embed.add_field(name="セッション数", value=str(session_count), inline=False)
    
    member_times = stats.get("member_times", {})
    if member_times:
        member_stats = ""
        for member_id, seconds in member_times.items():
            member_stats += f"<@{member_id}>: {str(datetime.timedelta(seconds=int(seconds)))}\n"
        embed.add_field(name="メンバー別通話時間", value=member_stats, inline=False)
    
    await interaction.response.send_message(embed=embed)

# -----------------------------
# Bot起動時の初期化
@bot.event
async def on_ready():
    load_channels_from_file()   # 通知先チャンネルの読み込み
    load_stats()                # 通話統計の読み込み
    await bot.tree.sync()       # スラッシュコマンドの同期
    print(f"Logged in as {bot.user.name}")
    print("現在の通知チャンネル設定:", server_notification_channels)

# -----------------------------
# Botの起動
async def main():
    await bot.start(TOKEN)

if __name__ == "__main__":
    asyncio.run(main())

