import discord
from discord.ext import commands, tasks
import os
import asyncio
import logging

import constants # constants モジュールをインポート

# 他のモジュールのインポート
from commands import BotCommands
from tasks import BotTasks
from voice_events import VoiceEvents, SleepCheckManager
from voice_state_manager import VoiceStateManager
import config
from database import init_db, close_db

# ロギングの設定
# 環境変数からロギングレベルを取得、設定されていなければ constants.LOGGING_LEVEL を使用
log_level = os.getenv('LOG_LEVEL', constants.LOGGING_LEVEL).upper()
logging.basicConfig(level=log_level, format=constants.LOGGING_FORMAT)

# 設定の読み込み (環境変数からトークンを取得)
TOKEN = os.getenv('DISCORD_BOT_TOKEN')
if TOKEN is None:
    logging.error("DISCORD_BOT_TOKEN 環境変数が設定されていません。")
    exit(1) # トークンがない場合は終了

# インテントの設定
intents = discord.Intents.all()

# Botのセットアップ
bot = commands.Bot(command_prefix=constants.COMMAND_PREFIX, intents=intents)

@bot.event
async def on_ready():
    logging.info(f'Logged in as {bot.user.name}')
    logging.info(f'Discord.py version: {discord.__version__}')

    # データベースの初期化
    await init_db()
    logging.info('Database initialized.')

    # SleepCheckManager と VoiceStateManager のインスタンスを作成
    sleep_check_manager = SleepCheckManager(bot)
    voice_state_manager = VoiceStateManager(bot)

    # Cog の追加
    # SleepCheckManager と VoiceStateManager のインスタンスを作成
    sleep_check_manager = SleepCheckManager(bot)
    voice_state_manager = VoiceStateManager(bot)

    # Cog の追加
    # VoiceEvents Cog は sleep_check_manager と voice_state_manager を必要とする
    voice_events_cog = VoiceEvents(bot, sleep_check_manager, voice_state_manager)
    await bot.add_cog(voice_events_cog)
    logging.info("VoiceEvents Cog を追加しました。")

    # BotCommands のインスタンスを作成 (Cogとしては追加しない)
    bot_commands_instance = BotCommands(bot, sleep_check_manager, voice_state_manager)
    logging.info("BotCommands インスタンスを作成しました。")

    # BotTasks Cog は bot_commands_instance を必要とする
    tasks_cog = BotTasks(bot, bot_commands_instance)
    await bot.add_cog(tasks_cog)
    logging.info("BotTasks Cog を追加しました。")

    # 定期実行タスクの開始
    tasks_cog.send_monthly_stats_task.start()
    tasks_cog.send_annual_stats_task.start()
    logging.info("定期実行タスクを開始しました。")

    # コマンドの手動登録と同期
    # BotCommands Cog を bot.add_cog() で追加するとコマンド同期がうまくいかないため、
    # ここでは BotCommands のインスタンスを作成し、各コマンドを手動でツリーに追加しています。
    logging.info("全ての参加ギルドに対してコマンドの手動登録と同期を開始します。")
    synced_guild_count = 0
    for guild in bot.guilds:
        logging.info(f'ギルド {guild.id} ({guild.name}) のコマンド登録を開始します。')
        try:
            # ギルドコマンドとしてツリーに追加
            bot.tree.add_command(bot_commands_instance.monthly_stats_callback, guild=guild)
            bot.tree.add_command(bot_commands_instance.total_time_callback, guild=guild)
            bot.tree.add_command(bot_commands_instance.call_ranking_callback, guild=guild)
            bot.tree.add_command(bot_commands_instance.call_duration_callback, guild=guild)
            bot.tree.add_command(bot_commands_instance.help_callback, guild=guild)
            bot.tree.add_command(bot_commands_instance.changesendchannel_callback, guild=guild)
            bot.tree.add_command(bot_commands_instance.debug_annual_stats_callback, guild=guild)
            bot.tree.add_command(bot_commands_instance.set_sleep_check_callback, guild=guild)

            # ギルドコマンドを同期
            synced_commands = await bot.tree.sync(guild=guild)
            logging.info(f'ギルド {guild.id} ({guild.name}) のコマンド同期に成功しました。同期されたコマンド数: {len(synced_commands)}')
            synced_guild_count += 1

        except Exception as e:
            logging.error(f'ギルド {guild.id} ({guild.name}) のコマンド登録または同期に失敗しました: {e}')

    logging.info(f'コマンド登録と同期が完了しました。{synced_guild_count} 個のギルドで同期に成功しました。')
    logging.info('Bot is ready.')

# Botの実行
if __name__ == "__main__":
    try:
        bot.run(TOKEN)
    except discord.errors.GatewayNotFound:
        logging.error("Invalid token was passed.")
    except Exception as e:
        logging.error(f"An error occurred during bot execution: {e}")
