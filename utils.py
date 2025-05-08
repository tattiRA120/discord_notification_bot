import discord
from discord.ext import commands, tasks
import os
import json
from dotenv import load_dotenv
from zoneinfo import ZoneInfo
import datetime

from database import get_total_call_time, update_member_monthly_stats, record_voice_session_to_db, get_guild_settings

# .envファイルの環境変数を読み込む
load_dotenv()

# 環境変数からトークンを取得
TOKEN = os.getenv('DISCORD_BOT_TOKEN')

intents = discord.Intents.default()
intents.voice_states = True
intents.members = True
intents.message_content = True
# bot = commands.Bot(command_prefix="!", intents=intents) # utils.pyでのボットインスタンス作成を削除

# サーバーごとの通知先チャンネルIDを保存する辞書
server_notification_channels = {}

# main.pyから設定されるボットインスタンス
bot = None

# 通話開始時間と最初に通話を開始した人を記録する辞書（通話通知用）
call_sessions = {}

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

# 通知チャンネルIDを取得する
def get_notification_channel_id(guild_id: int):
    return server_notification_channels.get(str(guild_id))

# 通知チャンネルIDを設定する
def set_notification_channel_id(guild_id: int, channel_id: int):
    server_notification_channels[str(guild_id)] = channel_id
    save_channels_to_file()

def convert_utc_to_jst(utc_time):
    return utc_time.astimezone(ZoneInfo("Asia/Tokyo"))

def format_duration(duration_seconds):
    seconds = int(duration_seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

# --- 二人以上の通話時間計算ヘルパー関数 ---
def calculate_call_duration_seconds(start_time):
    now = datetime.datetime.now(datetime.timezone.utc)
    return (now - start_time).total_seconds()

# アクティブな通話チャンネルとその通話時間を取得する
def get_active_call_durations(guild_id: int):
    active_calls = []
    now = datetime.datetime.now(datetime.timezone.utc)
    for key, session_data in active_voice_sessions.items():
        if key[0] == guild_id:
            channel = bot.get_channel(key[1])
            if channel and isinstance(channel, discord.VoiceChannel):
                duration_seconds = (now - session_data["session_start"]).total_seconds()
                formatted_duration = format_duration(duration_seconds)
                active_calls.append({"channel_name": channel.name, "duration": formatted_duration})
    return active_calls

# (guild_id, channel_id) をキーに、現在進行中の「2人以上通話セッション」を記録する
active_voice_sessions = {}

# 2人以上が通話中のチャンネルを追跡するセット
active_status_channels = set()

# --- ステータス通話時間更新タスク ---
@tasks.loop(seconds=15)
async def update_call_status_task():
    if active_status_channels:
        # active_status_channelsからステータスに表示するチャンネルを選択
        channel_key_to_display = next(iter(active_status_channels))
        guild_id, channel_id = channel_key_to_display
        guild = bot.get_guild(guild_id)
        channel = bot.get_channel(channel_id)

        if guild and channel and channel_key_to_display in active_voice_sessions:
            # 選択したチャンネルの通話時間を計算し、ステータスに設定
            session_data = active_voice_sessions[channel_key_to_display]
            duration_seconds = calculate_call_duration_seconds(session_data["session_start"])
            formatted_duration = format_duration(duration_seconds)
            activity = discord.CustomActivity(name=f"{channel.name}: {formatted_duration}")
            await bot.change_presence(activity=activity)
        else:
            # チャンネルが見つからない、またはactive_voice_sessionsにない場合はセットから削除しステータスをクリア
            active_status_channels.discard(channel_key_to_display)
            if not active_status_channels:
                 await bot.change_presence(activity=None)
    else:
        # 2人以上の通話がない場合はステータスをクリア
        await bot.change_presence(activity=None)

# --- 10時間達成通知用ヘルパー関数 ---
async def check_and_notify_milestone(member: discord.Member, guild: discord.Guild, before_total: float, after_total: float):
    guild_id = str(guild.id)
    if guild_id not in server_notification_channels:
        return # 通知先チャンネルが設定されていない場合は何もしない

    notification_channel_id = server_notification_channels[guild_id]
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
        embed.add_field(name="現在の総累計時間", value=format_duration(after_total), inline=False)
        embed.timestamp = datetime.datetime.now(ZoneInfo("Asia/Tokyo"))

        try:
            await notification_channel.send(embed=embed)
        except discord.Forbidden:
            print(f"エラー: チャンネル {notification_channel.name} ({notification_channel_id}) への送信権限がありません。")
        except Exception as e:
            print(f"通知送信中にエラーが発生しました: {e}")
