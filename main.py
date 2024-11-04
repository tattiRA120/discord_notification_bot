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
            server_notification_channels = json.load(f)
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

            jst_time = convert_utc_to_jst(start_time)  # JSTに変換

            embed = discord.Embed(title="通話開始", color=0xea958f)
            embed.set_thumbnail(url=f"{member.avatar.url}?size=128")  # ユーザーアイコンを表示
            embed.add_field(name="`チャンネル`", value=f"{after.channel.name}")
            embed.add_field(name="`始めた人`", value=f"{member.display_name}")
            embed.add_field(name="`開始時間`", value=f"{jst_time.strftime('%Y/%m/%d %H:%M:%S')}")  # JST表記

            # サーバーごとに設定された通知チャンネルにメッセージを送信
            if str(guild_id) in server_notification_channels:
                notification_channel = bot.get_channel(server_notification_channels[str(guild_id)])
                if notification_channel:
                    # @everyone と embed を一緒に送信
                    await notification_channel.send(
                        content="@everyone",
                        embed=embed,
                        allowed_mentions=discord.AllowedMentions(everyone=True)
                    )
                else:
                    print(f"通知チャンネルが見つかりません: ギルドID {guild_id}, チャンネルID {server_notification_channels[str(guild_id)]}")

    # 通話終了：通話チャンネルから全員抜けたら通知
    elif before.channel is not None and after.channel is None:
        voice_channel_id = before.channel.id  # 音声チャンネルごとに管理するためのID

        if guild_id in call_sessions and voice_channel_id in call_sessions[guild_id]:
            voice_channel = before.channel
            if len(voice_channel.members) == 0:
                session = call_sessions[guild_id].pop(voice_channel_id)
                start_time = session["start_time"]
                call_duration = datetime.datetime.now(datetime.timezone.utc) - start_time  # 通話時間を計算
                hours, remainder = divmod(call_duration.total_seconds(), 3600)
                minutes, seconds = divmod(remainder, 60)
                duration_str = f"{int(hours):02}:{int(minutes):02}:{int(seconds):02}"

                embed = discord.Embed(title="通話終了", color=0x938dfd)
                embed.add_field(name="`チャンネル`", value=f"{voice_channel.name}")
                embed.add_field(name="`通話時間`", value=f"{duration_str}")
                # 「通話終了」の際はアイコンを表示しないため、set_thumbnailは使用しない

                # サーバーごとに設定された通知チャンネルにメッセージを送信
                if str(guild_id) in server_notification_channels:
                    notification_channel = bot.get_channel(server_notification_channels[str(guild_id)])
                    if notification_channel:
                        await notification_channel.send(embed=embed)
                    else:
                        print(f"通知チャンネルが見つかりません: ギルドID {guild_id}, チャンネルID {server_notification_channels[str(guild_id)]}")

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

