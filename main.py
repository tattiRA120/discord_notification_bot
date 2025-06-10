import discord
from discord.ext import commands, tasks
import os
import asyncio
import logging
from dotenv import load_dotenv

import constants

# 他のモジュールのインポート
from commands import BotCommands
from tasks import BotTasks
from voice_events import VoiceEvents, SleepCheckManager
from voice_state_manager import VoiceStateManager, CallNotificationManager, StatisticalSessionManager, BotStatusUpdater
import config
from database import init_db

# ロギングの設定
# 環境変数からロギングレベルを取得、設定されていなければ constants.LOGGING_LEVEL を使用
log_level = os.getenv('LOG_LEVEL', constants.LOGGING_LEVEL).upper()
logging.basicConfig(level=log_level, format=constants.LOGGING_FORMAT)

# 設定の読み込み (環境変数からトークンを取得)
load_dotenv()
TOKEN = os.getenv('DISCORD_BOT_TOKEN')
if TOKEN is None:
    logging.error("DISCORD_BOT_TOKEN environment variable is not set.")
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
    try:
        await init_db()
        logging.info('Database initialized.')
    except Exception as e:
        logging.error(f"Database initialization failed: {e}")
        # エラー発生時はボットを終了する
        await bot.close()
        exit(1)

    # SleepCheckManager のインスタンスを作成
    sleep_check_manager = SleepCheckManager(bot)
    logging.info("SleepCheckManager instance created.")

    # VoiceStateManager を構成する各マネージャーのインスタンスを作成
    call_notification_manager = CallNotificationManager(bot)
    statistical_session_manager = StatisticalSessionManager()
    bot_status_updater = BotStatusUpdater(bot, statistical_session_manager)
    logging.info("Decomposed VoiceStateManager components instantiated.")

    # VoiceStateManager のインスタンスを作成し、分解したマネージャーを渡す
    voice_state_manager = VoiceStateManager(bot, call_notification_manager, statistical_session_manager, bot_status_updater)
    logging.info("VoiceStateManager instance created with decomposed components.")

    # Cog の追加
    # VoiceEvents Cog は sleep_check_manager と voice_state_manager を必要とする
    voice_events_cog = VoiceEvents(bot, sleep_check_manager, voice_state_manager)
    await bot.add_cog(voice_events_cog)
    logging.info("VoiceEvents Cog added.")

    # BotCommands のインスタンスを作成 (Cogとしては追加しない)
    # BotCommands は voice_state_manager を必要とする
    bot_commands_instance = BotCommands(bot, sleep_check_manager, voice_state_manager)
    logging.info("BotCommands instance created.")

    # BotTasks Cog は bot_commands_instance を必要とする
    tasks_cog = BotTasks(bot, bot_commands_instance)
    await bot.add_cog(tasks_cog)
    logging.info("BotTasks Cog added.")

    # 定期実行タスクの開始
    tasks_cog.send_monthly_stats_task.start()
    tasks_cog.send_annual_stats_task.start()
    # BotStatusUpdater のタスクは BotStatusUpdater クラス内で管理されるため、ここでは開始しない
    logging.info("Scheduled tasks started.")

    # スラッシュコマンドの手動登録と同期

    # 通常、Cogとして追加することでスラッシュコマンドは自動的に登録・同期されますが、
    # BotCommands Cog を bot.add_cog() で追加した場合にコマンド同期が安定しない問題が確認されています。
    # そのため、現状では回避策として、あえて各コマンドをギルドコマンドとして手動でツリーに追加し、同期を行っています。
    # この方法により、コマンドの登録と同期のプロセスをより確実に制御することを目指しています。
    logging.info("Starting manual command registration and synchronization for all joined guilds.")
    synced_guild_count = 0
    for guild in bot.guilds:
        logging.info(f'Starting command registration for guild {guild.id} ({guild.name}).')
        try:
            # ギルドコマンドとしてツリーに追加 (手動登録による回避策)
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
            logging.info(f'Successfully synced commands for guild {guild.id} ({guild.name}). Synced command count: {len(synced_commands)}')
            synced_guild_count += 1

        except Exception as e:
            logging.error(f'Failed to register or sync commands for guild {guild.id} ({guild.name}): {e}')

    logging.info(f'Command registration and synchronization completed. Successfully synced in {synced_guild_count} guilds.')
    logging.warning('Bot is ready.')

# Botの実行
if __name__ == "__main__":
    try:
        bot.run(TOKEN)
    except discord.errors.GatewayNotFound:
        logging.error("Invalid token was passed.")
    except Exception as e:
        logging.error(f"An error occurred during bot execution: {e}")
