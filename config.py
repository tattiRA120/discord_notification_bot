import os
import json

# サーバーごとの通知先チャンネルIDを保存する辞書
# キー: guild_id (str), 値: channel_id (int)
_server_notification_channels = {}

# 通知チャンネル設定を保存するファイルのパス
CHANNELS_FILE = "channels.json"

def save_channels_to_file():
    """通知チャンネル設定をファイルに保存する"""
    with open(CHANNELS_FILE, "w") as f:
        json.dump(_server_notification_channels, f)

def load_channels_from_file():
    """ファイルから通知チャンネル設定を読み込む"""
    global _server_notification_channels
    if os.path.exists(CHANNELS_FILE):
        with open(CHANNELS_FILE, "r") as f:
            try:
                content = f.read().strip()
                if content:
                    _server_notification_channels = json.loads(content)
                else:
                    _server_notification_channels = {}
            except json.JSONDecodeError:
                print(f"エラー: {CHANNELS_FILE} の読み込みに失敗しました。")
                _server_notification_channels = {}
    else:
        _server_notification_channels = {}
    # キーを文字列に統一
    _server_notification_channels = {str(guild_id): channel_id for guild_id, channel_id in _server_notification_channels.items()}

def get_notification_channel_id(guild_id: int):
    """指定されたギルドの通知チャンネルIDを取得する"""
    return _server_notification_channels.get(str(guild_id))

def set_notification_channel_id(guild_id: int, channel_id: int):
    """指定されたギルドの通知チャンネルIDを設定する"""
    _server_notification_channels[str(guild_id)] = channel_id
    save_channels_to_file()

# 初期ロード
load_channels_from_file()
