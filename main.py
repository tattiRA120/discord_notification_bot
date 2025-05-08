import discord
from discord.ext import commands
import os
from dotenv import load_dotenv

from database import init_db
from voice_events import VoiceEvents, SleepCheckManager
import utils
import commands as bot_commands
import config
import voice_state_manager
import formatters
import tasks # tasks モジュール全体をインポート

# .envファイルの環境変数を読み込む
load_dotenv()

# 環境変数からトークンを取得
TOKEN = os.getenv('DISCORD_BOT_TOKEN')

intents = discord.Intents.default()
intents.voice_states = True
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# --- 起動時処理 ---
@bot.event
async def on_ready():
    await init_db() # データベース初期化をon_readyで行う

    print(f'ログインしました: {bot.user.name}')

    # 依存関係のインスタンス化
    voice_state_manager_instance = voice_state_manager.VoiceStateManager(bot)
    sleep_check_manager_instance = SleepCheckManager(bot)

    # Cog のインスタンス化と追加
    await bot.add_cog(VoiceEvents(bot, sleep_check_manager_instance, voice_state_manager_instance))
    await bot.add_cog(tasks.BotTasks(bot))

    # BotCommands のインスタンスを作成
    bot_commands_instance = bot_commands.BotCommands(bot, sleep_check_manager_instance, voice_state_manager_instance)

    # Note: BotCommands Cog を bot.add_cog() で追加するとコマンド同期がうまくいかないため、
    # ここでは BotCommands のインスタンスを作成し、各コマンドを手動でツリーに追加しています。

    # 各ギルドに対してコマンドを手動でツリーに追加し、同期
    for guild in bot.guilds:
        print(f'接続中のサーバー: {guild.name} (ID: {guild.id})')
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
            print(f'ギルド {guild.name} ({guild.id}) のコマンド同期に成功しました。同期されたコマンド数: {len(synced_commands)}')

        except Exception as e:
            print(f'ギルド {guild.name} ({guild.id}) のコマンド同期に失敗しました: {e}')

    # タスクの開始
    # BotTasks Cog の _scheduled_stats タスクを開始
    bot_tasks_cog = bot.get_cog("BotTasks")
    if bot_tasks_cog and hasattr(bot_tasks_cog, '_scheduled_stats'):
        bot_tasks_cog._scheduled_stats.start()
        print("定期実行タスクを開始しました。")
    else:
        print("BotTasks Cog または _scheduled_stats タスクが見つかりませんでした。")


bot.run(TOKEN)
