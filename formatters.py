import datetime
from zoneinfo import ZoneInfo
import constants # constants モジュールをインポート

def format_duration(duration_seconds):
    """秒数を時間:分:秒の形式にフォーマットする"""
    seconds = int(duration_seconds)
    hours, remainder = divmod(seconds, constants.SECONDS_PER_HOUR)
    minutes, seconds = divmod(remainder, constants.SECONDS_PER_MINUTE)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

def convert_utc_to_jst(utc_time):
    """UTC時刻をJSTに変換する"""
    return utc_time.astimezone(ZoneInfo(constants.TIMEZONE_JST))
