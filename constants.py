# マジックナンバーを定義するファイル

import discord

# Time related constants
SECONDS_PER_MINUTE = 60
SECONDS_PER_HOUR = 3600
TIMEZONE_JST = "Asia/Tokyo"

# Database related constants
DB_FILE_NAME = "voice_stats.db"
TABLE_SETTINGS = "settings"
COLUMN_GUILD_ID = "guild_id"
COLUMN_LONELY_TIMEOUT_MINUTES = "lonely_timeout_minutes"
COLUMN_REACTION_WAIT_MINUTES = "reaction_wait_minutes"
TABLE_SESSIONS = "sessions"
COLUMN_MONTH_KEY = "month_key"
COLUMN_START_TIME = "start_time"
TABLE_SESSION_PARTICIPANTS = "session_participants"
COLUMN_SESSION_ID = "session_id"
COLUMN_MEMBER_ID = "member_id"
TABLE_MEMBER_MONTHLY_STATS = "member_monthly_stats"
COLUMN_TOTAL_DURATION = "total_duration"
TABLE_USER_MUTE_STATS = "user_mute_stats"
COLUMN_MUTE_COUNT = "mute_count"
DEFAULT_TOTAL_DURATION = 0
DEFAULT_LONELY_TIMEOUT_MINUTES = 180 # 3 hours
DEFAULT_REACTION_WAIT_MINUTES = 5

# Milestone related constants
MILESTONE_THRESHOLD_SECONDS = 36000 # マイルストーン通知の閾値（秒）

# Backup related constants
BACKUP_DIR_NAME = "backups"
NUM_BACKUP_FILES_TO_KEEP = 7
BACKUP_FILE_PREFIX = "voice_stats_"
DB_FILE_EXTENSION = ".db"
SQLITE_BACKUP_ALL_PAGES = 0

# Task related constants
CRON_MONTHLY_STATS = '0 18 1 * *' # 18:00 on the 1st of every month
CRON_ANNUAL_STATS = '0 18 31 12 *' # 18:00 on December 31st
STATS_SEND_HOUR = 18
STATS_SEND_MINUTE = 0
DAY_OF_MONTH_FIRST = 1
DAY_OF_YEAR_LAST = 31
MONTH_OF_YEAR_LAST = 12

# Logging related constants
LOGGING_LEVEL = "WARNING" # デフォルト値
LOGGING_FORMAT = '%(asctime)s - %(levelname)s - %(message)s'

# Bot related constants
COMMAND_PREFIX = '!'

# Embed related constants (Discord.py Follow the color standards of discord.Colour)
EMBED_COLOR_ERROR = 0xFF0000 # Red(Exceptionally not following the standards)
EMBED_COLOR_SUCCESS = 0x2ECC71 # Green
EMBED_COLOR_WARNING = 0xE67E22 # Orange
EMBED_COLOR_INFO = 0x3498DB # Blue
EMBED_COLOR_MILESTONE = 0xF1C40F # Gold
EMBED_COLOR_CALL_START = 0xE74C3C # Red
EMBED_COLOR_CALL_END = 0x5865F2 # Blurple

RANKING_LIMIT = 10 # ランキング表示件数

EMBED_TITLE_MONTHLY_STATS = "【前月の通話統計】"
EMBED_TITLE_ANNUAL_STATS = "【年間の通話統計】"
EMBED_TITLE_MILESTONE = "🎉 通話時間達成！ 🎉"
EMBED_TITLE_SLEEP_CHECK = "寝落ちミュート"
EMBED_TITLE_CALL_START = "通話開始"
EMBED_TITLE_CALL_END = "通話終了"
EMBED_TITLE_CURRENT_CALL_STATUS = "現在の通話状況"
EMBED_TITLE_TOTAL_CALL_RANKING = "総通話時間ランキング"
EMBED_TITLE_COMMAND_LIST = "コマンド一覧"

MESSAGE_NO_CALL_RECORDS = "は通話記録がありませんでした"
MESSAGE_NO_CALL_HISTORY = "通話履歴がありません"
MESSAGE_NO_RANKING_DATA = "通話時間データがありません"
MESSAGE_NO_ACTIVE_CALLS = "現在アクティブな通話はありません"
MESSAGE_NOTIFICATION_CHANNEL_ALREADY_SET = "通知チャンネルは既に {current_channel} に設定されています"
MESSAGE_NOTIFICATION_CHANNEL_SET = "通知チャンネルを {channel} に設定しました"
MESSAGE_CURRENT_SLEEP_CHECK_SETTINGS = "現在の寝落ち確認設定:\n"
MESSAGE_LONELY_TIMEOUT_MIN_ERROR = "一人以下の状態が続く時間は1分以上の整数で指定してください。"
MESSAGE_REACTION_WAIT_MIN_ERROR = "反応を待つ時間は1分以上の整数で指定してください。"
MESSAGE_SLEEP_CHECK_SETTINGS_UPDATED = "寝落ち確認設定を更新しました:\n"


EMBED_FIELD_MEMBER = "メンバー"
EMBED_FIELD_AVERAGE_CALL_TIME = "月間: 平均通話時間"
EMBED_FIELD_LONGEST_CALL = "月間: 最長通話"
EMBED_FIELD_CALL_RANKING = "月間: 通話時間ランキング"
EMBED_FIELD_TOTAL_CALL_TIME = "総通話時間"
EMBED_FIELD_LONELY_TIMEOUT = "一人以下の状態が続く時間"
EMBED_FIELD_REACTION_WAIT = "反応を待つ時間"
EMBED_FIELD_ACHIEVED_TIME = "達成時間"
EMBED_FIELD_CURRENT_TOTAL = "現在の総累計時間"
EMBED_FIELD_CHANNEL = "チャンネル"
EMBED_FIELD_STARTED_BY = "始めた人"
EMBED_FIELD_START_TIME = "開始時間"
EMBED_FIELD_CALL_DURATION = "通話時間"

EMBED_DESCRIPTION_SLEEP_CHECK = " さん、{channel_name} chで一人になってから時間が経ちました。\n寝落ちしていませんか？反応がない場合、自動でサーバーミュートします。\nミュートをキャンセルする場合は、 :white_check_mark: を押してください。"
EMBED_DESCRIPTION_SLEEP_CHECK_CANCEL = " さんが反応しました。\nサーバーミュートをキャンセルしました。"
EMBED_DESCRIPTION_SLEEP_CHECK_MUTE = " さんからの反応がなかったため、サーバーミュートしました。\n再入室するとサーバーミュートが解除されます。"
EMBED_DESCRIPTION_UNMUTE_ON_REJOIN = " さんが再入室したため、サーバーミュートを解除しました。"


# Voice state related constants
MIN_MEMBERS_FOR_SESSION = 2
STATUS_UPDATE_INTERVAL_SECONDS = 15
UNMUTE_DELAY_SECONDS = 1

# Reaction related constants
REACTION_EMOJI_SLEEP_CHECK = "✅"

# Mention related constants
MENTION_EVERYONE = "@everyone"
ALLOWED_MENTIONS_EVERYONE = discord.AllowedMentions(everyone=True)

# Config related constants
CHANNELS_FILE_NAME = "channels.json"
