import discord 
from discord import app_commands 
from discord.ext import commands, tasks
import datetime
import os
import json
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

# .envファイルの環境変数を読み込む
load_dotenv()
TOKEN = os.getenv('DISCORD_BOT_TOKEN')

intents = discord.Intents.default()
intents.voice_states = True
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# 通知先チャンネル設定（既存）
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
    server_notification_channels = {str(guild_id): channel_id for guild_id, channel_id in server_notification_channels.items()}

def convert_utc_to_jst(utc_time):
    return utc_time.astimezone(ZoneInfo("Asia/Tokyo"))

# -----------------------------
# 【統計用変数・関数】（先の実装例と同様）
ongoing_voice_sessions = {}  # {guild_id: {channel_id: {start_time, participants}}}
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
# 【イベント処理：on_voice_state_update】
@bot.event
async def on_voice_state_update(member, before, after):
    guild_id = member.guild.id

    # 対象のチャンネル（入室 or 退室）
    channel = after.channel if after.channel else before.channel
    if channel is None:
        return
    channel_id = channel.id
    current_count = len(channel.members)

    if guild_id not in ongoing_voice_sessions:
        ongoing_voice_sessions[guild_id] = {}

    # 2人以上になったタイミングで計測開始
    if current_count >= 2 and channel_id not in ongoing_voice_sessions[guild_id]:
        start_time = datetime.datetime.now(datetime.timezone.utc)
        ongoing_voice_sessions[guild_id][channel_id] = {
            "start_time": start_time,
            "participants": [m.id for m in channel.members]
        }
    # 2人以上から1人以下になったタイミングで計測終了
    elif current_count < 2 and channel_id in ongoing_voice_sessions[guild_id]:
        session = ongoing_voice_sessions[guild_id].pop(channel_id)
        session_start = session["start_time"]
        session_end = datetime.datetime.now(datetime.timezone.utc)
        participants = session["participants"]
        update_stats(guild_id, channel, session_start, session_end, participants)

    # 以下は既存の通話開始／終了通知（全員退出時のみ）
    if before.channel is None and after.channel is not None:
        voice_channel_id = after.channel.id
        if guild_id not in call_sessions:
            call_sessions[guild_id] = {}
        if voice_channel_id not in call_sessions[guild_id]:
            start_time = datetime.datetime.now(datetime.timezone.utc)
            call_sessions[guild_id][voice_channel_id] = {"start_time": start_time, "first_member": member.id}
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
    elif before.channel is not None and after.channel is None:
        voice_channel_id = before.channel.id
        if guild_id in call_sessions and voice_channel_id in call_sessions[guild_id]:
            voice_channel = before.channel
            if len(voice_channel.members) == 0:
                session = call_sessions[guild_id].pop(voice_channel_id)
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
# 管理者のみ実行可能な年間統計コマンド（/stats_year）
@bot.tree.command(name="stats_year", description="年間の通話統計を表示します（管理者限定）。")
async def stats_year(interaction: discord.Interaction):
    # 管理者チェック
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("このコマンドは管理者のみ実行可能です。", ephemeral=True)
        return

    now = datetime.datetime.now(datetime.timezone.utc)
    jst_now = convert_utc_to_jst(now)
    guild_id = str(interaction.guild.id)

    if guild_id not in server_monthly_stats:
        await interaction.response.send_message("統計情報がありません。", ephemeral=True)
        return

    # 12月31日以外の場合はテスト用として、1月1日から実行日までのデータを集計
    if not (jst_now.month == 12 and jst_now.day == 31):
        year = jst_now.year
        # 1月から現在月までのキーリスト
        keys = [f"{year}-{month:02d}" for month in range(1, jst_now.month + 1)]
    else:
        # 正式な年間統計（すべての月のデータ）
        keys = list(server_monthly_stats[guild_id].keys())

    yearly_data = {"total_multi_time": 0, "sessions_count": 0, "member_times": {}}
    for month_key in keys:
        if month_key in server_monthly_stats[guild_id]:
            data = server_monthly_stats[guild_id][month_key]
            yearly_data["total_multi_time"] += data["total_multi_time"]
            yearly_data["sessions_count"] += len(data["sessions"])
            for member_id, sec in data["member_times"].items():
                yearly_data["member_times"][member_id] = yearly_data["member_times"].get(member_id, 0) + sec

    avg_seconds = (yearly_data["total_multi_time"] / yearly_data["sessions_count"]) if yearly_data["sessions_count"] else 0
    ranking = sorted(yearly_data["member_times"].items(), key=lambda x: x[1], reverse=True)
    ranking_text = ""
    for rank, (member_id, seconds) in enumerate(ranking, 1):
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        seconds_rem = int(seconds % 60)
        ranking_text += f"{rank}. <@{member_id}> : {hours:02}:{minutes:02}:{seconds_rem:02}\n"

    title = f"{jst_now.year} 年間通話統計"
    if not (jst_now.month == 12 and jst_now.day == 31):
        title += " (テスト用)"
    embed = discord.Embed(title=title, color=0x0000ff)
    embed.add_field(name="平均通話時間", value=str(datetime.timedelta(seconds=int(avg_seconds))), inline=False)
    embed.add_field(name="総通話時間", value=str(datetime.timedelta(seconds=int(yearly_data['total_multi_time']))), inline=False)
    embed.add_field(name="参加者ランキング", value=ranking_text if ranking_text else "データなし", inline=False)
    await interaction.response.send_message(embed=embed)

# -----------------------------
# 月次統計コマンド（既存）
@bot.tree.command(name="stats_month", description="今月の通話統計を表示します")
async def stats_month(interaction: discord.Interaction):
    guild_id = str(interaction.guild.id)
    now = datetime.datetime.now(datetime.timezone.utc)
    month_key = get_month_key(now)
    if guild_id in server_monthly_stats and month_key in server_monthly_stats[guild_id]:
        data = server_monthly_stats[guild_id][month_key]
        total_seconds = data["total_multi_time"]
        avg_seconds = total_seconds / len(data["sessions"]) if data["sessions"] else 0
        if data["sessions"]:
            longest = max(data["sessions"], key=lambda x: x["duration"])
            longest_duration = longest["duration"]
            longest_date = convert_utc_to_jst(datetime.datetime.fromisoformat(longest["start"])).strftime("%Y/%m/%d (%a)")
        else:
            longest_duration = 0
            longest_date = "N/A"

        ranking = sorted(data["member_times"].items(), key=lambda x: x[1], reverse=True)
        ranking_text = ""
        for rank, (member_id, seconds) in enumerate(ranking, 1):
            hours = int(seconds // 3600)
            minutes = int((seconds % 3600) // 60)
            seconds_rem = int(seconds % 60)
            ranking_text += f"{rank}. <@{member_id}> : {hours:02}:{minutes:02}:{seconds_rem:02}\n"

        embed = discord.Embed(title=f"{month_key} の通話統計", color=0x00ff00)
        embed.add_field(name="平均通話時間", value=str(datetime.timedelta(seconds=int(avg_seconds))), inline=False)
        embed.add_field(name="最長通話時間", value=str(datetime.timedelta(seconds=int(longest_duration))), inline=False)
        embed.add_field(name="最長通話日", value=longest_date, inline=False)
        embed.add_field(name="参加者ランキング", value=ranking_text if ranking_text else "データなし", inline=False)
    else:
        embed = discord.Embed(title="統計情報", description="今月の通話データがありません。", color=0xff0000)
    await interaction.response.send_message(embed=embed)

# -----------------------------
# 定期処理：12月31日正午のみ自動で年間統計を送信する
@tasks.loop(minutes=1)
async def check_period():
    now = datetime.datetime.now(datetime.timezone.utc)
    jst_now = convert_utc_to_jst(now)
    # 月次集計は既存のロジック（ここでは省略）
    # ...
    # 自動実行：12月31日正午（JST 12:00）に年間統計を各通知チャンネルへ送信
    if jst_now.month == 12 and jst_now.day == 31 and jst_now.hour == 12 and jst_now.minute == 0:
        for guild_id, channel_id in server_notification_channels.items():
            channel = bot.get_channel(channel_id)
            if channel:
                if guild_id in server_monthly_stats:
                    yearly_data = {"total_multi_time": 0, "sessions_count": 0, "member_times": {}}
                    for month_key, data in server_monthly_stats[guild_id].items():
                        yearly_data["total_multi_time"] += data["total_multi_time"]
                        yearly_data["sessions_count"] += len(data["sessions"])
                        for member_id, sec in data["member_times"].items():
                            yearly_data["member_times"][member_id] = yearly_data["member_times"].get(member_id, 0) + sec
                    avg_seconds = (yearly_data["total_multi_time"] / yearly_data["sessions_count"]) if yearly_data["sessions_count"] else 0
                    ranking = sorted(yearly_data["member_times"].items(), key=lambda x: x[1], reverse=True)
                    ranking_text = ""
                    for rank, (member_id, seconds) in enumerate(ranking, 1):
                        hours = int(seconds // 3600)
                        minutes = int((seconds % 3600) // 60)
                        seconds_rem = int(seconds % 60)
                        ranking_text += f"{rank}. <@{member_id}> : {hours:02}:{minutes:02}:{seconds_rem:02}\n"
                    embed = discord.Embed(title=f"{jst_now.year} 年間通話統計", color=0x0000ff)
                    embed.add_field(name="平均通話時間", value=str(datetime.timedelta(seconds=int(avg_seconds))), inline=False)
                    embed.add_field(name="総通話時間", value=str(datetime.timedelta(seconds=int(yearly_data['total_multi_time']))), inline=False)
                    embed.add_field(name="参加者ランキング", value=ranking_text if ranking_text else "データなし", inline=False)
                    await channel.send(embed=embed)
                else:
                    await channel.send("年間の通話データはありません。")

check_period.start()

# -----------------------------
# 通知先チャンネル変更コマンド（既存）
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

@bot.event
async def on_ready():
    load_channels_from_file()
    load_stats()
    await bot.tree.sync()
    print(f"Logged in as {bot.user.name}")
    print("現在の通知チャンネル設定:", server_notification_channels)

bot.run(TOKEN)

