import discord
from discord.ext import commands
import os
import json
from dotenv import load_dotenv

from database import init_db
from voice_events import on_voice_state_update
from tasks import scheduled_stats
import utils
import commands as bot_commands

# .envファイルの環境変数を読み込む
load_dotenv()

# 環境変数からトークンを取得
TOKEN = os.getenv('DISCORD_BOT_TOKEN')

intents = discord.Intents.default()
intents.voice_states = True
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# utilsモジュールにボットインスタンスを設定
utils.bot = bot

# --- 起動時処理 ---
@bot.event
async def on_ready():
    utils.load_channels_from_file() # utilsモジュールの関数を呼び出す
    await init_db() # データベース初期化をon_readyで行う

    print(f'ログインしました: {bot.user.name}')

    # グローバルコマンドを削除
    bot.tree.clear_commands(guild=None)
    await bot.tree.sync()

    # voice_eventsモジュールのイベントハンドラを登録
    bot.add_listener(on_voice_state_update, 'on_voice_state_update')

    # 各ギルドに対してコマンドを登録・同期
    for guild in bot.guilds:
        print(f'接続中のサーバー: {guild.name} (ID: {guild.id})')

        # commandsモジュールのスラッシュコマンドをギルドコマンドとして登録
        bot.tree.add_command(bot_commands.monthly_stats, guild=guild)
        bot.tree.add_command(bot_commands.total_time, guild=guild)
        bot.tree.add_command(bot_commands.call_ranking, guild=guild)
        bot.tree.add_command(bot_commands.call_duration, guild=guild)
        bot.tree.add_command(bot_commands.help, guild=guild)
        bot.tree.add_command(bot_commands.changesendchannel, guild=guild)
        bot.tree.add_command(bot_commands.debug_annual_stats, guild=guild)
        bot.tree.add_command(bot_commands.set_sleep_check, guild=guild)

        try:
            # ギルドコマンドを同期
            synced_commands = await bot.tree.sync(guild=guild)
            print(f'ギルド {guild.name} ({guild.id}) のコマンド同期に成功しました。')
        except Exception as e:
            print(f'ギルド {guild.name} ({guild.id}) のコマンド同期に失敗しました: {e}')

    scheduled_stats.start()

bot.run(TOKEN)
