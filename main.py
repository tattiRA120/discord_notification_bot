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
active_voice_sessions = {}

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

# 指定されたセッションの開始月に、対象メンバーのdurationを加算して保存する
def update_member_stats(member_id, session_start, duration):
    month_key = session_start.strftime("%Y-%m")
    if month_key not in voice_stats:
        voice_stats[month_key] = {"sessions": [], "members": {}}
    voice_stats[month_key]["members"][str(member_id)] = voice_stats[month_key]["members"].get(str(member_id), 0) + duration
    save_voice_stats()

# record_voice_session はセッション全体のレコードを記録する（update_members=False で個別更新を行わない）
def record_voice_session(session_start, session_duration, participants, update_members=True):
    """
    session_start: datetime (UTC) セッション開始時刻
    session_duration: 秒数
    participants: list of member IDs (int)
    """
    month_key = session_start.strftime("%Y-%m")
    if month_key not in voice_stats:
        voice_stats[month_key] = {"sessions": [], "members": {}}
    voice_stats[month_key]["sessions"].append({
        "start_time": session_start.isoformat(),
        "duration": session_duration,
        "participants": participants
    })
    if update_members:
        for m in participants:
            voice_stats[month_key]["members"][str(m)] = voice_stats[month_key]["members"].get(str(m), 0) + session_duration
    save_voice_stats()

# --- 年間統計作成用ヘルパー関数 ---
def create_annual_stats_embed(guild, year: str):
    """
    year: "YYYY"形式の文字列
    対象年度の各月の統計情報を集計し、全体の年間統計情報のembedを作成
    """
    # 対象年度の月キーを集める（例："2025-01", ..."2025-12"）
    sessions_all = []
    members_total = {}
    for month_key, data in voice_stats.items():
        if month_key.startswith(f"{year}-"):
            sessions = data.get("sessions", [])
            members = data.get("members", {})
            sessions_all.extend(sessions)
            for m_id, dur in members.items():
                members_total[m_id] = members_total.get(m_id, 0) + dur

    year_display = f"{year}年"
    if not sessions_all:
        return None, year_display

    total_duration = sum(sess["duration"] for sess in sessions_all)
    total_sessions = len(sessions_all)
    avg_duration = total_duration / total_sessions if total_sessions else 0

    # 最長セッション
    longest_session = max(sessions_all, key=lambda s: s["duration"])
    longest_duration = longest_session["duration"]
    longest_date = convert_utc_to_jst(datetime.datetime.fromisoformat(longest_session["start_time"])).strftime('%Y/%m/%d')
    longest_participants = longest_session["participants"]
    longest_participants_names = []
    for mid in longest_participants:
        m_obj = guild.get_member(mid)
        if m_obj:
            longest_participants_names.append(m_obj.display_name)
        else:
            longest_participants_names.append(str(mid))
    longest_info = f"{format_duration(longest_duration)}（{longest_date}）\n参加: {', '.join(longest_participants_names)}"

    # メンバー別ランキング（累計時間）
    sorted_members = sorted(members_total.items(), key=lambda x: x[1], reverse=True)
    ranking_lines = []
    for i, (member_id, duration) in enumerate(sorted_members, start=1):
        m_obj = guild.get_member(int(member_id))
        name = m_obj.display_name if m_obj else str(member_id)
        ranking_lines.append(f"{i}.  {format_duration(duration)}  {name}")
    ranking_text = "\n".join(ranking_lines) if ranking_lines else "なし"

    embed = discord.Embed(title=f"【{year_display}】年間通話統計情報", color=0x00ff00)
    embed.add_field(name="年間: 平均通話時間", value=f"{format_duration(avg_duration)}", inline=False)
    embed.add_field(name="年間: 最長通話", value=longest_info, inline=False)
    embed.add_field(name="年間: 通話時間ランキング", value=ranking_text, inline=False)
    return embed, year_display

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
            embed = discord.Embed(title="通話開始", color=0xE74C3C)
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
                embed = discord.Embed(title="通話終了", color=0x5865F2)
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

    # --- 2人以上通話状態の記録（各メンバーごとに個別記録＋全参加者リストを維持する処理） ---

    """
    active_voice_sessions は { (guild_id, channel_id): {
         "session_start": datetime,
         "current_members": { member_id: join_time, ... },
         "all_participants": set([member_id, ...])
    } }
    """
    # 対象チャンネル（入室または退室対象）
    channel_before = before.channel
    channel_after = after.channel

    # 同一チャンネル内での状態変化の場合は何もしない
    if channel_before == channel_after:
        return

    # 退室処理（before.channel から退出した場合）
    if channel_before is not None:
        key = (guild_id, channel_before.id)
        if key in active_voice_sessions:
            session_data = active_voice_sessions[key]
            # もし対象メンバーが在室中ならその個人分の退室処理を実施
            if member.id in session_data["current_members"]:
                join_time = session_data["current_members"].pop(member.id)
                duration = (now - join_time).total_seconds()
                member_call_times[member.id] = member_call_times.get(member.id, 0) + duration
                update_member_stats(member.id, join_time, duration)
            # もし退室後、チャンネル内人数が1人以下ならセッション終了処理を実施
            if channel_before.members is not None and len(channel_before.members) < 2:
                for m_id, join_time in session_data["current_members"].copy().items():
                    d = (now - join_time).total_seconds()
                    member_call_times[m_id] = member_call_times.get(m_id, 0) + d
                    update_member_stats(m_id, join_time, d)
                    session_data["current_members"].pop(m_id)
                overall_duration = (now - session_data["session_start"]).total_seconds()
                record_voice_session(session_data["session_start"], overall_duration, list(session_data["all_participants"]), update_members=False)
                active_voice_sessions.pop(key, None)

    # 入室処理（after.channelに入室した場合）
    if channel_after is not None:
        key = (guild_id, channel_after.id)
        # チャンネル内の人数が2人以上の場合
        if len(channel_after.members) >= 2:
            if key not in active_voice_sessions:
                # セッション開始時刻は、通話が2人以上になった時刻（この時点の now）
                active_voice_sessions[key] = {
                    "session_start": now,
                    "current_members": { m.id: now for m in channel_after.members },
                    "all_participants": set(m.id for m in channel_after.members)
                }
            else:
                # 既存のセッションがある場合、新たに入室したメンバーを更新する
                session_data = active_voice_sessions[key]
                for m in channel_after.members:
                    if m.id not in session_data["current_members"]:
                        session_data["current_members"][m.id] = now
                    session_data["all_participants"].add(m.id)
        else:
            # 人数が2人未満の場合は、既にセッションが存在する場合のみ更新する
            if key in active_voice_sessions:
                session_data = active_voice_sessions[key]
                for m in channel_after.members:
                    if m.id not in session_data["current_members"]:
                        session_data["current_members"][m.id] = now
                    session_data["all_participants"].add(m.id)

# --- /call_stats コマンド ---
@bot.tree.command(name="call_stats", description="月間の二人以上が参加していた通話の統計情報を表示します")
@app_commands.describe(month="表示する年月（形式: YYYY-MM）省略時は今月")
async def call_stats(interaction: discord.Interaction, month: str = None):
    if month is None:
        now = datetime.datetime.now(datetime.timezone.utc)
        current_month = now.strftime("%Y-%m")
    else:
        current_month = month

    try:
        year, mon = current_month.split("-")
        month_display = f"{year}年{mon}月"
    except Exception as e:
        await interaction.response.send_message("指定された月の形式が正しくありません。形式は YYYY-MM で指定してください。", ephemeral=True)
        return

    load_voice_stats()

    if current_month not in voice_stats:
        await interaction.response.send_message(f"{month_display}は通話統計情報が記録されていません", ephemeral=True)
        return

    monthly_data = voice_stats.get(current_month, {"sessions": [], "members": {}})
    sessions = monthly_data["sessions"]
    member_stats = monthly_data["members"]

    if sessions:
        monthly_avg = sum(sess["duration"] for sess in sessions) / len(sessions)
    else:
        monthly_avg = 0

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

    await interaction.response.send_message(embed=embed, ephemeral=True)

# --- /total_time コマンド ---
@bot.tree.command(name="total_time", description="メンバーの通話総累計時間を表示します")
async def total_time(interaction: discord.Interaction, member: discord.Member = None):
    load_voice_stats()  # 最新のデータを読み込む
    member = member or interaction.user  # デフォルトはコマンド送信者
    total_seconds = sum(
        stats["members"].get(str(member.id), 0)
        for stats in voice_stats.values()
    )

    embed = discord.Embed(title="通話総累計時間", color=discord.Color.blue())
    embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)
    
    if total_seconds == 0:
        embed.description = "通話履歴はありません。"
    else:
        formatted_time = format_duration(total_seconds)
        embed.add_field(name="累計通話時間", value=formatted_time, inline=False)
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

# 管理者用：通知先チャンネル変更コマンド
@bot.tree.command(name="changesendchannel", description="管理者用: 通知先のチャンネルを変更します")
@app_commands.describe(channel="通知を送信するチャンネル")
async def changesendchannel(interaction: discord.Interaction, channel: discord.TextChannel):
    # 管理者権限のチェック
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("このコマンドは管理者専用です。", ephemeral=True)
        return

    guild_id = str(interaction.guild.id)
    if guild_id in server_notification_channels and server_notification_channels[guild_id] == channel.id:
        current_channel = bot.get_channel(server_notification_channels[guild_id])
        await interaction.response.send_message(f"すでに {current_channel.mention} で設定済みです。", ephemeral=True)
    else:
        server_notification_channels[guild_id] = channel.id
        save_channels_to_file()
        await interaction.response.send_message(f"通知先のチャンネルが {channel.mention} に設定されました。", ephemeral=True)

# 管理者用：年間統計情報送信デバッグコマンド
@bot.tree.command(name="debug_annual_stats", description="管理者用: 年間統計情報送信をデバッグします")
@app_commands.describe(year="表示する年度（形式: YYYY）。省略時は今年")
async def debug_annual_stats(interaction: discord.Interaction, year: str = None):
    # 管理者権限のチェック
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("このコマンドは管理者専用です。", ephemeral=True)
        return

    # 年度の指定がなければ現在の年度を使用
    if year is None:
        now = datetime.datetime.now(ZoneInfo("Asia/Tokyo"))
        year = str(now.year)
    
    load_voice_stats()
    embed, display = create_annual_stats_embed(interaction.guild, year)
    if embed:
        await interaction.response.send_message(embed=embed, ephemeral=True)
    else:
        await interaction.response.send_message(f"{display}の通話統計情報が記録されていません", ephemeral=True)

# --- 毎日20時に統計情報を送信するタスク ---
@tasks.loop(time=datetime.time(hour=20, minute=0, tzinfo=ZoneInfo("Asia/Tokyo")))
async def scheduled_stats():
    now = datetime.datetime.now(ZoneInfo("Asia/Tokyo"))
    load_voice_stats()  # 最新の統計情報をロード

    # 前月の統計情報送信（毎月1日の場合）
    if now.day == 1:
        first_day_current = now.replace(day=1)
        prev_month_last_day = first_day_current - datetime.timedelta(days=1)
        previous_month = prev_month_last_day.strftime("%Y-%m")
        
        for guild_id, channel_id in server_notification_channels.items():
            guild = bot.get_guild(int(guild_id))
            channel = bot.get_channel(channel_id)
            if guild and channel:
                embed, month_display = create_monthly_stats_embed(guild, previous_month)
                if embed:
                    await channel.send(embed=embed)
                else:
                    await channel.send(f"{month_display}は通話統計情報が記録されていません")
    
    # 年間統計情報送信（12月31日の場合）
    if now.month == 12 and now.day == 31:
        year_str = str(now.year)
        for guild_id, channel_id in server_notification_channels.items():
            guild = bot.get_guild(int(guild_id))
            channel = bot.get_channel(channel_id)
            if guild and channel:
                embed, year_display = create_annual_stats_embed(guild, year_str)
                if embed:
                    await channel.send(embed=embed)
                else:
                    await channel.send(f"{year_display}の通話統計情報が記録されていません")

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
    scheduled_stats.start()

bot.run(TOKEN)

