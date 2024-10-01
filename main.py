import discord
from discord import app_commands
from discord.ext import commands
import datetime
import os
from dotenv import load_dotenv

# .envファイルの環境変数を読み込む
load_dotenv()

# 環境変数からトークンを取得
TOKEN = os.getenv('DISCORD_BOT_TOKEN')

intents = discord.Intents.default()
intents.voice_states = True  # ボイスチャンネルの変更イベントを有効にする
intents.members = True  # メンバー情報の取得を許可する

bot = commands.Bot(command_prefix="!", intents=intents)

# サーバーごとの通知先チャンネルIDを保存する辞書
server_notification_channels = {}

# ユーザーごとに通話開始時間を記録するための辞書
call_start_times = {}

# 通話開始・終了時に通知するためのイベント
@bot.event
async def on_voice_state_update(member, before, after):
    # 通話開始
    if before.channel is None and after.channel is not None:
        start_time = datetime.datetime.utcnow()  # 現在時刻をUTCで取得
        call_start_times[member.id] = start_time  # ユーザーIDをキーにして開始時間を記録

        embed = discord.Embed(title="通話開始", color=0xea958f)
        embed.set_thumbnail(url=member.avatar.url)
        embed.add_field(name="`チャンネル`", value=f"{after.channel.name}")
        embed.add_field(name="`始めた人`", value=f"{member.display_name}")
        embed.add_field(name="`開始時間`", value=f"{start_time.strftime('%Y/%m/%d %H:%M:%S UTC')}")

        # サーバーごとに設定された通知チャンネルにメッセージを送信
        guild_id = member.guild.id
        if guild_id in server_notification_channels:
            notification_channel = bot.get_channel(server_notification_channels[guild_id])
            if notification_channel:
                await notification_channel.send(embed=embed)

    # 通話終了
    elif before.channel is not None and after.channel is None:
        # 通話開始時間が記録されている場合、通話時間を計算
        if member.id in call_start_times:
            start_time = call_start_times.pop(member.id)  # 開始時間を取得して辞書から削除
            call_duration = datetime.datetime.utcnow() - start_time  # 通話時間を計算
            hours, remainder = divmod(call_duration.total_seconds(), 3600)
            minutes, seconds = divmod(remainder, 60)
            duration_str = f"{int(hours):02}:{int(minutes):02}:{int(seconds):02}"

            embed = discord.Embed(title="`通話終了`", color=0x938dfd)
            embed.set_thumbnail(url=member.avatar.url)
            embed.add_field(name="`チャンネル`", value=f"{before.channel.name}")
            embed.add_field(name="`通話時間`", value=f"{duration_str}")

            # サーバーごとに設定された通知チャンネルにメッセージを送信
            guild_id = member.guild.id
            if guild_id in server_notification_channels:
                notification_channel = bot.get_channel(server_notification_channels[guild_id])
                if notification_channel:
                    await notification_channel.send(embed=embed)

# 通知先チャンネルを変更するためのスラッシュコマンド
@bot.tree.command(name="changesendchannel", description="通知先のチャンネルを変更します")
@app_commands.describe(channel="通知を送信するチャンネル")
async def changesendchannel(interaction: discord.Interaction, channel: discord.TextChannel):
    # 通知先チャンネルを変更
    server_notification_channels[interaction.guild.id] = channel.id
    await interaction.response.send_message(f"通知先のチャンネルが {channel.mention} に設定されました。")

# Botの起動時にスラッシュコマンドを同期する
@bot.event
async def on_ready():
    await bot.tree.sync()  # スラッシュコマンドを同期
    print(f"Logged in as {bot.user.name}")

# Botを実行
bot.run(TOKEN)

