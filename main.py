import discord
from discord.ext import commands, tasks
import os
import asyncio
import logging
import sys
import traceback
from dotenv import load_dotenv

import constants
import config
from database import init_db
from formatters import create_log_embed

# 他のモジュールのインポート
from commands import BotCommands
from tasks import BotTasks
from voice_events import VoiceEvents, SleepCheckManager
from voice_state_manager import VoiceStateManager, CallNotificationManager, StatisticalSessionManager, BotStatusUpdater

# ロギングの設定
# 環境変数からロギングレベルを取得、設定されていなければ constants.LOGGING_LEVEL を使用
log_level = os.getenv('LOG_LEVEL', constants.LOGGING_LEVEL).upper()
logging.basicConfig(level=log_level, format=constants.LOGGING_FORMAT)
logger = logging.getLogger() # ルートロガーを取得

# カスタムロギングハンドラ
class DiscordHandler(logging.Handler):
    def __init__(self, bot_instance):
        super().__init__()
        self.bot = bot_instance
        self.setFormatter(logging.Formatter(constants.LOGGING_FORMAT))
        self.sent_messages = []  # 送信済みのメッセージを保存するリスト
        self.max_messages = 10  # 保存するメッセージの最大数

    def emit(self, record):
        if not self.bot.is_ready():
            return # ボットが準備できていない場合は送信しない

        message = record.getMessage()
        if message in self.sent_messages:
            return  # 同じメッセージが既に送信されている場合は送信しない

        if record.levelno < logging.WARNING:
            return # WARNING未満のログはDiscordに送信しない

        # メッセージを送信済みのリストに追加
        self.sent_messages.append(message)
        if len(self.sent_messages) > self.max_messages:
            self.sent_messages.pop(0)  # 古いメッセージを削除

        # 「Bot is ready.」メッセージはDiscordに送信しない
        if record.getMessage() == 'Bot is ready.':
            return

        # Discordに送信するタスクを非同期で実行
        self.bot.loop.create_task(self.send_log_to_discord(record))

    async def send_log_to_discord(self, record):
        try:
            embed = create_log_embed(record)
            for guild in self.bot.guilds:
                channel_id = config.get_notification_channel_id(guild.id)
                if channel_id:
                    channel = self.bot.get_channel(channel_id)
                    if channel:
                        try:
                            await channel.send(embed=embed)
                        except discord.Forbidden:
                            logging.warning(f"Bot does not have permission to send messages to channel {channel.id} in guild {guild.name}.")
                        except Exception as e:
                            logging.error(f"Failed to send log embed to Discord channel {channel.id}: {e}")
        except Exception as e:
            logging.error(f"Error in DiscordHandler.send_log_to_discord: {e}", exc_info=True)

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

    # DiscordHandler をロガーに追加
    discord_handler = DiscordHandler(bot)
    logger.addHandler(discord_handler)
    logging.info("DiscordHandler added to logger.")

    # データベースの初期化
    try:
        await init_db()
        logging.info('Database initialized.')
    except Exception as e:
        logging.error(f"Database initialization failed: {e}", exc_info=True)
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
    if 'VoiceEvents' not in bot.cogs:
        await bot.add_cog(voice_events_cog)
        logging.info("VoiceEvents Cog added.")
    else:
        logging.info("VoiceEvents Cog already loaded.")

    # BotCommands のインスタンスを作成 (Cogとしては追加しない)
    # BotCommands は voice_state_manager を必要とする
    bot_commands_instance = BotCommands(bot, sleep_check_manager, voice_state_manager)
    logging.info("BotCommands instance created.")

    # BotTasks Cog は bot_commands_instance を必要とする
    tasks_cog = BotTasks(bot, bot_commands_instance)
    if 'BotTasks' not in bot.cogs:
        await bot.add_cog(tasks_cog)
        logging.info("BotTasks Cog added.")
    else:
        logging.info("BotTasks Cog already loaded.")

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
            # 既存のギルドコマンドをクリアしてから再登録することで、CommandAlreadyRegistered エラーを回避
            await bot.tree.clear_commands(guild=guild)
            bot.tree.add_command(bot_commands_instance.stats, guild=guild)
            bot.tree.add_command(bot_commands_instance.help_callback, guild=guild)
            bot.tree.add_command(bot_commands_instance.changesendchannel_callback, guild=guild)
            bot.tree.add_command(bot_commands_instance.debug_annual_stats_callback, guild=guild)
            bot.tree.add_command(bot_commands_instance.set_sleep_check_callback, guild=guild)

            # ギルドコマンドを同期
            synced_commands = await bot.tree.sync(guild=guild)
            logging.info(f'Successfully synced commands for guild {guild.id} ({guild.name}). Synced command count: {len(synced_commands)}')
            synced_guild_count += 1

        except Exception as e:
            logging.error(f'Failed to register or sync commands for guild {guild.id} ({guild.name}): {e}', exc_info=True)

    logging.info(f'Command registration and synchronization completed. Successfully synced in {synced_guild_count} guilds.')
    logging.warning('Bot is ready.')

@bot.event
async def on_command_error(ctx, error):
    """コマンド実行中にエラーが発生した場合のハンドラ"""
    if isinstance(error, commands.CommandNotFound):
        return # コマンドが見つからない場合は無視
    
    logging.error(f"Command error in guild {ctx.guild.id} ({ctx.guild.name}) by {ctx.author.name}: {error}", exc_info=True)
    
    # エラーメッセージをユーザーに送信
    embed = discord.Embed(
        title="コマンドエラー",
        description=f"コマンドの実行中にエラーが発生しました。\n```\n{error}\n```",
        color=constants.EMBED_COLOR_ERROR
    )
    try:
        await ctx.send(embed=embed)
    except discord.Forbidden:
        logging.warning(f"Bot does not have permission to send error messages to channel {ctx.channel.id} in guild {ctx.guild.name}.")

@bot.event
async def on_error(event, *args, **kwargs):
    """Discord.py内部で発生するエラーのハンドラ"""
    logging.error(f"Unhandled Discord.py event error: {event}", exc_info=True)
    # 必要に応じて、ここで特定のチャンネルにエラーを送信することも可能

# Botの実行
if __name__ == "__main__":
    try:
        bot.run(TOKEN)
    except discord.errors.GatewayNotFound:
        logging.error("Invalid token was passed.", exc_info=True)
    except Exception as e:
        logging.error(f"An error occurred during bot execution: {e}", exc_info=True)
        # ボットが予期せず停止した場合にDiscordに通知を試みる
        # この時点では bot.is_ready() が False の可能性があるため、直接ログハンドラを呼び出す
        # ただし、DiscordHandler が bot インスタンスに依存するため、ここでは直接ログ出力に留める
        # または、別途Webhookなどを用いて通知する仕組みを検討する
        # 現状は、logging.error が DiscordHandler を通じて通知されることを期待する
        sys.exit(1)
