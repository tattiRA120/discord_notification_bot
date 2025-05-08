import datetime
from zoneinfo import ZoneInfo

def format_duration(duration_seconds):
    """秒数を時間:分:秒の形式にフォーマットする"""
    seconds = int(duration_seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

def convert_utc_to_jst(utc_time):
    """UTC時刻をJSTに変換する"""
    return utc_time.astimezone(ZoneInfo("Asia/Tokyo"))
