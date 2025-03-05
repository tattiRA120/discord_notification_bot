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

# 環境変数からトークンを取得
TOKEN = os.getenv('DISCORD_BOT_TOKEN')

intents = discord.Intents.default()
intents.voice_states = True  # ボイスチャンネルの変更イベントを有効にする
intents.members = True       # メンバー情報の取得を許可する
intents.message_content = True  # メッセージ内容のアクセスを許可

bot = commands.Bot(command_prefix="!", intents=intents)

# サーバーごとの通知先チャンネルIDを保存する辞書
server_notification_channels = {}

# 通話開始時間と最初に通話を開始した人を記録する辞書（通話通知用）
call_sessions = {}

# 各メンバーの通話時間を記録する辞書（通話通知用）
member_call_times = {}

# 通知チャンネル設定を保存するファイルのパス
CHANNELS_FILE = "channels.json"

def save_channels_to_file():
    with open(CHANNELS_FILE, "w") as f:
        json.dump(server_notification_channels, f)

def load_channels_from_file():
    global server_notification_channels
    if os.path.exists(CHANNELS_FILE):
        with open(CHANNELS_FILE, "r") as f:
            try:
                content = f.read().strip()
                if content:
                    server_notification_channels = json.loads(content)
                else:
                    server_notification_channels = {}
            except json.JSONDecodeError:
                print(f"エラー: {CHANNELS_FILE} の読み込みに失敗しました。")
                server_notification_channels = {}
    else:
        server_notification_channels = {}

    # キーを文字列に統一
    server_notification_channels = {str(guild_id): channel_id for guild_id, channel_id in server_notification_channels.items()}

def convert_utc_to_jst(utc_time):
    return utc_time.astimezone(ZoneInfo("Asia/Tokyo"))

def format_duration(duration_seconds):
    """秒数を '00:00:00' 表記に変換"""
    seconds = int(duration_seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

# (guild_id, channel_id) をキーに、現在進行中の「2人以上通話セッション」を記録する
active_voice_sessions = {}  # {(guild_id, channel_id): {"start_time": datetime, "participants": [member_id, ...]}}

# 月間の通話統計を記録するファイル
VOICE_STATS_FILE = "voice_stats.json"
voice_stats = {}  # 例: { "2025-03": { "sessions": [ {"start_time": ISO, "duration": 秒, "participants": [member_id,...] }, ... ], "members": { member_id: 累計秒数, ... } } }

def load_voice_stats():
    global voice_stats
    if os.path.exists(VOICE_STATS_FILE):
        with open(VOICE_STATS_FILE, "r") as f:
            try:
                content = f.read().strip()
                if content:
                    voice_stats = json.loads(content)
                else:
                    voice_stats = {}
            except json.JSONDecodeError:
                print(f"エラー: {VOICE_STATS_FILE} の読み込みに失敗しました。")
                voice_stats = {}
    else:
        voice_stats = {}

def save_voice_stats():
    with open(VOICE_STATS_FILE, "w") as f:
        json.dump(voice_stats, f, indent=2)

def record_voice_session(session_start, session_duration, participants):
    """
    session_start: datetime (UTC) セッション開始時刻
    session_duration: 秒数
    participants: list of member IDs (int)
    """
    month_key = session_start.strftime("%Y-%m")  # 例: "2025-03"
    if month_key not in voice_stats:
        voice_stats[month_key] = {"sessions": [], "members": {}}
    voice_stats[month_key]["sessions"].append({
        "start_time": session_start.isoformat(),
        "duration": session_duration,
        "participants": participants
    })
    for m in participants:
        voice_stats[month_key]["members"][str(m)] = voice_stats[month_key]["members"].get(str(m), 0) + session_duration
    save_voice_stats()

# --- イベントハンドラ ---
@bot.event
async def on_voice_state_update(member, before, after):
    guild_id = member.guild.id
    now = datetime.datetime.now(datetime.timezone.utc)

    # 通話通知機能
    if before.channel is None and after.channel is not None:
        voice_channel_id = after.channel.id
        if guild_id not in call_sessions:
            call_sessions[guild_id] = {}
        if voice_channel_id not in call_sessions[guild_id]:
            start_time = now
            call_sessions[guild_id][voice_channel_id] = {"start_time": start_time, "first_member": member.id}
            jst_time = convert_utc_to_jst(start_time)
            embed = discord.Embed(title="通話開始", color=0xea958f)
            embed.set_thumbnail(url=f"{member.avatar.url}?size=128")
            embed.add_field(name="チャンネル", value=f"{after.channel.name}")
            embed.add_field(name="始めた人", value=f"{member.display_name}")
            embed.add_field(name="開始時間", value=f"{jst_time.strftime('%Y/%m/%d %H:%M:%S')}")
            if str(guild_id) in server_notification_channels:
                notification_channel = bot.get_channel(server_notification_channels[str(guild_id)])
                if notification_channel:
                    await notification_channel.send(content="@everyone", embed=embed, allowed_mentions=discord.AllowedMentions(everyone=True))
                else:
                    print(f"通知チャンネルが見つかりません: ギルドID {guild_id}")
    elif before.channel is not None and after.channel is None:
        voice_channel_id = before.channel.id
        if guild_id in call_sessions and voice_channel_id in call_sessions[guild_id]:
            voice_channel = before.channel
            if len(voice_channel.members) == 0:
                session = call_sessions[guild_id].pop(voice_channel_id)
                start_time = session["start_time"]
                call_duration = (now - start_time).total_seconds()
                duration_str = format_duration(call_duration)
                embed = discord.Embed(title="通話終了", color=0x938dfd)
                embed.add_field(name="チャンネル", value=f"{voice_channel.name}")
                embed.add_field(name="通話時間", value=f"{duration_str}")
                if str(guild_id) in server_notification_channels:
                    notification_channel = bot.get_channel(server_notification_channels[str(guild_id)])
                    if notification_channel:
                        await notification_channel.send(embed=embed)
                    else:
                        print(f"通知チャンネルが見つかりません: ギルドID {guild_id}")
                for m in voice_channel.members:
                    m_id = m.id
                    member_call_times[m_id] = member_call_times.get(m_id, 0) + call_duration

    # --- 二人以上通話状態の記録 ---
    # 判定対象は、現在いるチャンネルが「二人以上」になっているかどうかです。
    if after.channel is not None:
        vc = after.channel
        key = (guild_id, vc.id)
        if len(vc.members) >= 2:
            # セッションが開始されていなければ開始
            if key not in active_voice_sessions:
                active_voice_sessions[key] = {"start_time": now, "participants": [m.id for m in vc.members]}
        else:
            # activeなセッションがあれば終了
            if key in active_voice_sessions:
                session = active_voice_sessions.pop(key)
                session_duration = (now - session["start_time"]).total_seconds()
                if session_duration > 0:
                    record_voice_session(session["start_time"], session_duration, session["participants"])
    if before.channel is not None:
        vc = before.channel
        key = (guild_id, vc.id)
        if key in active_voice_sessions and len(vc.members) < 2:
            session = active_voice_sessions.pop(key)
            session_duration = (now - session["start_time"]).total_seconds()
            if session_duration > 0:
                record_voice_session(session["start_time"], session_duration, session["participants"])

# --- /call_stats コマンド ---
@bot.tree.command(name="call_stats", description="月間の二人以上が参加していた通話の統計情報を表示します")
@app_commands.describe(month="表示する年月（形式: YYYY-MM）省略時は今月")
async def call_stats(interaction: discord.Interaction, month: str = None):
    # 指定がなければ現在の月を使用
    if month is None:
        now = datetime.datetime.now(datetime.timezone.utc)
        current_month = now.strftime("%Y-%m")
    else:
        current_month = month

    # current_month を「YYYY年MM月」形式に変換
    try:
        year, mon = current_month.split("-")
        month_display = f"{year}年{mon}月"
    except Exception as e:
        await interaction.response.send_message("指定された月の形式が正しくありません。形式は YYYY-MM で指定してください。", ephemeral=True)
        return

    load_voice_stats()

    # 統計情報が存在しない場合
    if current_month not in voice_stats:
        await interaction.response.send_message(f"{month_display}は通話統計情報が記録されていません", ephemeral=True)
        return

    monthly_data = voice_stats.get(current_month, {"sessions": [], "members": {}})
    sessions = monthly_data["sessions"]
    member_stats = monthly_data["members"]

    # 月間平均（セッションごとの平均時間）
    if sessions:
        monthly_avg = sum(sess["duration"] for sess in sessions) / len(sessions)
    else:
        monthly_avg = 0

    # 月間最長セッション
    if sessions:
        longest_session = max(sessions, key=lambda s: s["duration"])
        longest_duration = longest_session["duration"]
        longest_date = convert_utc_to_jst(datetime.datetime.fromisoformat(longest_session["start_time"])).strftime('%Y/%m/%d')
        longest_participants = longest_session["participants"]
        longest_participants_names = []
        for mid in longest_participants:
            m_obj = interaction.guild.get_member(mid)
            if m_obj:
                longest_participants_names.append(m_obj.display_name)
            else:
                longest_participants_names.append(str(mid))
        longest_info = f"{format_duration(longest_duration)}（{longest_date}）\n参加: {', '.join(longest_participants_names)}"
    else:
        longest_info = "なし"

    # メンバー別ランキング（累計時間）
    sorted_members = sorted(member_stats.items(), key=lambda x: x[1], reverse=True)
    ranking_lines = []
    for i, (member_id, duration) in enumerate(sorted_members, start=1):
        m_obj = interaction.guild.get_member(int(member_id))
        name = m_obj.display_name if m_obj else str(member_id)
        ranking_lines.append(f"{i}.  {format_duration(duration)}  {name}")
    ranking_text = "\n".join(ranking_lines) if ranking_lines else "なし"

    embed = discord.Embed(title=f"【{month_display}】通話統計情報", color=0x00ff00)
    embed.add_field(name="平均通話時間", value=f"{format_duration(monthly_avg)}", inline=False)
    embed.add_field(name="最長通話", value=longest_info, inline=False)
    embed.add_field(name="通話時間ランキング", value=ranking_text, inline=False)

    # 送信時に ephemeral=True を指定して送信者のみ表示
    await interaction.response.send_message(embed=embed, ephemeral=True)

# --- 通知先チャンネル変更コマンド ---
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

# --- 毎月1日20時に前月の統計情報を送信するタスク ---
@tasks.loop(time=datetime.time(hour=20, minute=0, tzinfo=ZoneInfo("Asia/Tokyo")))
async def scheduled_previous_month_stats():
    now = datetime.datetime.now(ZoneInfo("Asia/Tokyo"))
    # JSTで毎日20:00に実行されるが、日付が1日の場合のみ処理する
    if now.day == 1:
        # 前月を算出（例：2025年03月1日20時 → 前月は2025-02）
        first_day_current = now.replace(day=1)
        prev_month_last_day = first_day_current - datetime.timedelta(days=1)
        previous_month = prev_month_last_day.strftime("%Y-%m")
        
        load_voice_stats()  # 最新の統計情報をロード

        for guild_id, channel_id in server_notification_channels.items():
            guild = bot.get_guild(int(guild_id))
            channel = bot.get_channel(channel_id)
            if guild and channel:
                embed, month_display = create_monthly_stats_embed(guild, previous_month)
                if embed:
                    await channel.send(embed=embed)
                else:
                    await channel.send(f"{month_display}は通話統計情報が記録されていません")

# --- 起動時処理 ---
@bot.event
async def on_ready():
    load_channels_from_file()
    load_voice_stats()
    try:
        await bot.tree.sync()
        print("スラッシュコマンドが正常に同期されました。")
    except Exception as e:
        print(f"スラッシュコマンドの同期に失敗しました: {e}")
    print(f"Logged in as {bot.user.name}")
    print("現在の通知チャンネル設定:", server_notification_channels)

bot.run(TOKEN)

