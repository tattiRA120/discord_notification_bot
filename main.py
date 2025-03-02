import discord
from discord import app_commands
from discord.ext import commands
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
intents.members = True  # メンバー情報の取得を許可する
intents.message_content = True  # メッセージ内容のアクセスを許可

bot = commands.Bot(command_prefix="!", intents=intents)

# サーバーごとの通知先チャンネルIDを保存する辞書
server_notification_channels = {}

# 通話開始時間と最初に通話を開始した人を記録する辞書
call_sessions = {}

# 各メンバーの通話時間を記録する辞書
member_call_times = {}

# 通知チャンネル設定を保存するファイルのパス
CHANNELS_FILE = "channels.json"

# 通知チャンネル設定をファイルに保存する関数
def save_channels_to_file():
    with open(CHANNELS_FILE, "w") as f:
        json.dump(server_notification_channels, f)

# 通知チャンネル設定をファイルから読み込む関数
def load_channels_from_file():
    global server_notification_channels
    if os.path.exists(CHANNELS_FILE):
        with open(CHANNELS_FILE, "r") as f:
            try:
                # ファイルが空でないか確認
                content = f.read().strip()
                if content:
                    server_notification_channels = json.loads(content)
                else:
                    server_notification_channels = {}  # ファイルが空なら初期化
            except json.JSONDecodeError:
                print(f"エラー: {CHANNELS_FILE} の読み込みに失敗しました。空のファイルまたは不正な形式です。")
                server_notification_channels = {}  # エラーが発生した場合は空の辞書で初期化
    else:
        server_notification_channels = {}  # ファイルが存在しない場合も空の辞書で初期化

    # 重複するキーが存在しないようにする
    server_notification_channels = {str(guild_id): channel_id for guild_id, channel_id in server_notification_channels.items()}

# UTCからJSTに変換する関数 (astimezone方式に変更)
def convert_utc_to_jst(utc_time):
    jst_time = utc_time.astimezone(ZoneInfo("Asia/Tokyo"))  # JSTに変換
    return jst_time

# 通話開始・終了時に通知するためのイベント
@bot.event
async def on_voice_state_update(member, before, after):
    guild_id = member.guild.id

    # 通話開始：誰もいない通話チャンネルに入ったら通知
    if before.channel is None and after.channel is not None:
        voice_channel_id = after.channel.id  # 音声チャンネルごとに管理するためのID

        if guild_id not in call_sessions:
            call_sessions[guild_id] = {}  # サーバーごとに音声チャンネル辞書を初期化

        if voice_channel_id not in call_sessions[guild_id]:
            start_time = datetime.datetime.now(datetime.timezone.utc)  # 現在時刻をUTCで取得
            call_sessions[guild_id][voice_channel_id] = {"start_time": start_time, "first_member": member.id}

    # 通話終了：通話チャンネルから全員抜けたら通知
    elif before.channel is not None and after.channel is None:
        voice_channel_id = before.channel.id  # 音声チャンネルごとに管理するためのID

        if guild_id in call_sessions and voice_channel_id in call_sessions[guild_id]:
            voice_channel = before.channel
            if len(voice_channel.members) == 0:
                session = call_sessions[guild_id].pop(voice_channel_id)
                start_time = session["start_time"]
                call_duration = datetime.datetime.now(datetime.timezone.utc) - start_time  # 通話時間を計算

                # 通話時間をメンバーごとに記録
                for member in voice_channel.members:
                    member_id = member.id
                    duration_seconds = call_duration.total_seconds()

                    if member_id not in member_call_times:
                        member_call_times[member_id] = 0
                    member_call_times[member_id] += duration_seconds

# 通話統計情報を表示するスラッシュコマンド
@bot.tree.command(name="call_stats", description="通話統計情報を表示します")
async def call_stats(interaction: discord.Interaction):
    guild_id = str(interaction.guild.id)

    # 平均通話時間、最長通話時間を計算
    total_call_duration = 0
    longest_call_duration = 0
    longest_call_date = ""
    call_count = 0

    for session in call_sessions.get(guild_id, {}).values():
        start_time = session["start_time"]
        end_time = datetime.datetime.now(datetime.timezone.utc)
        call_duration = end_time - start_time

        total_call_duration += call_duration.total_seconds()
        call_count += 1

        if call_duration.total_seconds() > longest_call_duration:
            longest_call_duration = call_duration.total_seconds()
            longest_call_date = convert_utc_to_jst(start_time).strftime('%Y/%m/%d')

    if call_count > 0:
        avg_call_duration = total_call_duration / call_count
    else:
        avg_call_duration = 0

    # 通話時間ランキング
    sorted_members = sorted(member_call_times.items(), key=lambda x: x[1], reverse=True)

    # 結果を表示
    embed = discord.Embed(title="通話統計情報", color=0x00ff00)
    embed.add_field(name="平均通話時間", value=f"{avg_call_duration / 3600:.2f} 時間")
    embed.add_field(name="最長通話時間", value=f"{longest_call_duration / 3600:.2f} 時間")
    embed.add_field(name="最長通話日付", value=longest_call_date)

    embed.add_field(name="通話時間ランキング", value="\n".join([f"{interaction.guild.get_member(member_id).display_name}: {duration / 3600:.2f} 時間" for member_id, duration in sorted_members[:5]]), inline=False)

    await interaction.response.send_message(embed=embed)

# 通知先チャンネルを変更するためのスラッシュコマンド
@bot.tree.command(name="changesendchannel", description="通知先のチャンネルを変更します")
@app_commands.describe(channel="通知を送信するチャンネル")
async def changesendchannel(interaction: discord.Interaction, channel: discord.TextChannel):
    guild_id = str(interaction.guild.id)

    # 既に設定されているチャンネルと同じ場合は保存せずに通知
    if guild_id in server_notification_channels and server_notification_channels[guild_id] == channel.id:
        current_channel = bot.get_channel(server_notification_channels[guild_id])
        await interaction.response.send_message(f"すでに {current_channel.mention} で設定済みです。")
    else:
        # 通知先チャンネルを変更
        server_notification_channels[guild_id] = channel.id
        save_channels_to_file()  # チャンネル設定を保存
        await interaction.response.send_message(f"通知先のチャンネルが {channel.mention} に設定されました。")

# Botの起動時にスラッシュコマンドを同期し、通知チャンネル設定をロードする
@bot.event
async def on_ready():
    load_channels_from_file()  # 通知チャンネル設定をロード
    await bot.tree.sync()  # スラッシュコマンドを同期
    print(f"Logged in as {bot.user.name}")
    print("現在の通知チャンネル設定:", server_notification_channels)

# Botを実行
bot.run(TOKEN)

