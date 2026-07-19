import discord
from discord.ext import commands
import os
import logging
import sys
from dotenv import load_dotenv
import aiohttp

import constants
from database import init_db
from formatters import create_log_embed

# 他のモジュールのインポート
from commands import BotCommands
from tasks import BotTasks
from voice_events import VoiceEvents, SleepCheckManager
from voice_state_manager import (
    VoiceStateManager,
    CallNotificationManager,
    StatisticalSessionManager,
    BotStatusUpdater,
)

# ロギングの設定
# 環境変数からロギングレベルを取得、設定されていなければ constants.LOGGING_LEVEL を使用
log_level = os.getenv("LOG_LEVEL", constants.LOGGING_LEVEL).upper()
logging.basicConfig(level=log_level, format=constants.LOGGING_FORMAT)
logger = logging.getLogger()  # ルートロガーを取得

# 内部ログ出力用のロガー（DiscordHandler自身のエラーや情報出力を逃がし、無限ループを防ぐ）
internal_logger = logging.getLogger("bot.internal")
internal_logger.propagate = False
internal_console_handler = logging.StreamHandler(sys.stderr)
internal_console_handler.setFormatter(logging.Formatter(constants.LOGGING_FORMAT))
internal_logger.addHandler(internal_console_handler)
# 内部ロガーのレベルはルートロガーと同期、またはINFO以上とする
internal_logger.setLevel(logging.INFO)


# カスタムロギングハンドラ
class DiscordHandler(logging.Handler):
    def __init__(self, bot_instance):
        super().__init__()
        self.bot = bot_instance
        self.setFormatter(logging.Formatter(constants.LOGGING_FORMAT))
        self.sent_messages = []  # 送信済みのメッセージを保存するリスト
        self.max_messages = 10  # 保存するメッセージの最大数
        self.buffer = []  # 未送信ログのバッファ
        self.max_buffer_size = 100  # バッファの最大件数
        self.is_flushing = False  # フラッシュ処理の排他フラグ
        self.webhook_url = os.getenv("DISCORD_WEBHOOK_URL")

    def emit(self, record):
        # 内部ロガー自身のログはDiscordに送信しない（無限ループ防止）
        if record.name == "bot.internal":
            return

        message = record.getMessage()
        if message in self.sent_messages:
            return  # 同じメッセージが既に送信されている場合は送信しない

        if record.levelno < logging.WARNING:
            return  # WARNING未満のログはDiscordに送信しない

        # 「Bot is ready.」メッセージはDiscordに送信しない
        if message == "Bot is ready.":
            return

        # メッセージを送信済みのリストに追加
        self.sent_messages.append(message)
        if len(self.sent_messages) > self.max_messages:
            self.sent_messages.pop(0)  # 古いメッセージを削除

        # Webhook URLが設定されていない場合は何もしない
        if not self.webhook_url:
            return

        # Webhookでの送信はBot自体の接続状況に依存しないため、
        # イベントループが動いていれば即座に非同期タスクとして送信を試みる
        if self.bot.loop and self.bot.loop.is_running():
            self.bot.loop.create_task(self.send_log_to_discord(record))
        else:
            # イベントループが稼働していない場合はバッファに保存する
            self.add_to_buffer(record)

    def add_to_buffer(self, record):
        # バッファにログを追加する（最大件数制限あり）
        self.buffer.append(record)
        if len(self.buffer) > self.max_buffer_size:
            self.buffer.pop(0)  # 最も古いログを破棄

    async def flush_buffer(self):
        # バッファに溜まったログのフラッシュを試みる
        if not self.webhook_url or self.is_flushing or not self.buffer:
            return
        if not (self.bot.loop and self.bot.loop.is_running()):
            return
        self.is_flushing = True
        try:
            internal_logger.info(
                f"Flushing {len(self.buffer)} buffered logs to Discord Webhook..."
            )
            while self.buffer:
                record = self.buffer[0]
                success = await self._send_single_log(record)
                if success:
                    self.buffer.pop(0)
                else:
                    # 送信に失敗した場合は一旦終了（ネットワーク未接続など）
                    internal_logger.warning(
                        "Failed to flush buffer log via Webhook, pausing flush process."
                    )
                    break
        finally:
            self.is_flushing = False

    async def _send_single_log(self, record):
        # 実際にWebhookへ1件のログを送信する
        if not self.webhook_url:
            return False
        try:
            embed = create_log_embed(record)
            async with aiohttp.ClientSession() as session:
                webhook = discord.Webhook.from_url(self.webhook_url, session=session)
                await webhook.send(embed=embed)
            return True
        except Exception as e:
            internal_logger.error(f"Failed to send log embed to Discord Webhook: {e}")
            return False

    async def send_log_to_discord(self, record):
        success = await self._send_single_log(record)
        if not success:
            self.add_to_buffer(record)
        # 溜まっているバッファがあれば送信を試みる
        if self.buffer and self.bot.loop and self.bot.loop.is_running():
            self.bot.loop.create_task(self.flush_buffer())


# 設定の読み込み (環境変数からトークンを取得)
load_dotenv()
TOKEN = os.getenv("DISCORD_BOT_TOKEN")
if TOKEN is None:
    logging.error("DISCORD_BOT_TOKEN environment variable is not set.")
    exit(1)  # トークンがない場合は終了

# インテントの設定
intents = discord.Intents.all()

# Botのセットアップ
bot = commands.Bot(command_prefix=constants.COMMAND_PREFIX, intents=intents)


@bot.event
async def on_ready():
    if bot.user:
        logging.info(f"Logged in as {bot.user.name}")
    logging.info(f"Discord.py version: {discord.__version__}")

    # ボットが完全に準備できるまで待機
    await bot.wait_until_ready()
    logging.info("Bot is fully ready, proceeding with setup.")

    if bot.tree is None:
        logging.error(
            "bot.tree is None. This should not happen after wait_until_ready."
        )
        await bot.close()
        exit(1)
    else:
        logging.info(f"bot.tree type: {type(bot.tree)}")
        logging.info(f"bot.tree.clear_commands type: {type(bot.tree.clear_commands)}")

    # DiscordHandler をロガーに追加
    discord_handler = DiscordHandler(bot)
    logger.addHandler(discord_handler)
    logging.info("DiscordHandler added to logger.")
    bot.loop.create_task(discord_handler.flush_buffer())

    # データベースの初期化
    try:
        await init_db()
        logging.info("Database initialized.")
    except Exception as e:
        logging.error(f"Database initialization failed: {e}", exc_info=True)
        # エラー発生時はボットを終了する
        await bot.close()
        exit(1)

    # SleepCheckManager のインスタンスを作成
    sleep_check_manager = SleepCheckManager(bot)
    logging.info("SleepCheckManager instance created.")

    # DBからアクティブなミュートメンバーをロード
    await sleep_check_manager.load_active_muted_members()

    # VoiceStateManager を構成する各マネージャーのインスタンスを作成
    call_notification_manager = CallNotificationManager(bot)
    statistical_session_manager = StatisticalSessionManager(bot)

    bot_status_updater = BotStatusUpdater(bot, statistical_session_manager)
    logging.info("Decomposed VoiceStateManager components instantiated.")

    # VoiceStateManager のインスタンスを作成し、分解したマネージャーを渡す
    voice_state_manager = VoiceStateManager(
        bot, call_notification_manager, statistical_session_manager, bot_status_updater
    )
    logging.info("VoiceStateManager instance created with decomposed components.")

    # Cog の追加
    # VoiceEvents Cog は sleep_check_manager と voice_state_manager を必要とする
    voice_events_cog = VoiceEvents(bot, sleep_check_manager, voice_state_manager)
    if "VoiceEvents" not in bot.cogs:
        await bot.add_cog(voice_events_cog)
        logging.info("VoiceEvents Cog added.")
    else:
        logging.info("VoiceEvents Cog already loaded.")

    # BotCommands をCogとして追加する
    bot_commands_instance = BotCommands(bot, sleep_check_manager, voice_state_manager)
    if "BotCommands" not in bot.cogs:
        await bot.add_cog(bot_commands_instance)
        logging.info("BotCommands Cog added.")
    else:
        logging.info("BotCommands Cog already loaded.")

    # BotTasks Cog は bot_commands_instance を必要とする
    tasks_cog = BotTasks(bot, bot_commands_instance)
    if "BotTasks" not in bot.cogs:
        await bot.add_cog(tasks_cog)
        logging.info("BotTasks Cog added.")
    else:
        logging.info("BotTasks Cog already loaded.")

    # 定期実行タスクの開始
    tasks_cog.send_monthly_stats_task.start()
    tasks_cog.send_annual_stats_task.start()
    # BotStatusUpdater のタスクは BotStatusUpdater クラス内で管理されるため、ここでは開始しない
    logging.info("Scheduled tasks started.")

    # スラッシュコマンドの手動登録と同期の修正
    # BotCommands を Cog として追加することで自動的にツリーに登録されます。
    # 各ギルドで即座にコマンドを利用可能にするため、グローバルコマンドを各ギルドにコピーして同期します。
    if not getattr(bot, "_commands_registered", False):
        bot._commands_registered = True  # type: ignore[attr-defined]
        logging.info("Starting command synchronization for all joined guilds.")
        synced_guild_count = 0
        for guild in bot.guilds:
            if guild is None or bot.tree is None:
                continue

            logging.info(f"Syncing commands for guild {guild.id} ({guild.name}).")
            try:
                # グローバルコマンドをこのギルドにコピーして即座に反映させる
                bot.tree.copy_global_to(guild=guild)
                # ギルドコマンドを同期
                synced_commands = await bot.tree.sync(guild=guild)
                logging.info(
                    f"Successfully synced commands for guild {guild.id} ({guild.name}). Synced command count: {len(synced_commands)}"
                )
                synced_guild_count += 1
            except Exception as e:
                logging.error(
                    f"Failed to sync commands for guild {guild.id} ({guild.name}): {e}",
                    exc_info=True,
                )

        logging.info(
            f"Command synchronization completed. Successfully synced in {synced_guild_count} guilds."
        )
    logging.warning("Bot is ready.")


@bot.event
async def on_command_error(ctx, error):
    """コマンド実行中にエラーが発生した場合のハンドラ"""
    if isinstance(error, commands.CommandNotFound):
        return  # コマンドが見つからない場合は無視

    logging.error(
        f"Command error in guild {ctx.guild.id} ({ctx.guild.name}) by {ctx.author.name}: {error}",
        exc_info=True,
    )

    # エラーメッセージをユーザーに送信
    embed = discord.Embed(
        title="コマンドエラー",
        description=f"コマンドの実行中にエラーが発生しました。\n```\n{error}\n```",
        color=constants.EMBED_COLOR_ERROR,
    )
    try:
        await ctx.send(embed=embed)
    except discord.Forbidden:
        logging.warning(
            f"Bot does not have permission to send error messages to channel {ctx.channel.id} in guild {ctx.guild.name}."
        )


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
