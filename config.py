import os
import json
import logging # logging モジュールをインポート
import constants # constants モジュールをインポート

# ロガーを取得
logger = logging.getLogger(__name__)

# サーバーごとの通知先チャンネルIDを保存する辞書
# キー: guild_id (str), 値: channel_id (int)
_server_notification_channels = {}

# 通知チャンネル設定を保存するファイルのパス
CHANNELS_FILE = constants.CHANNELS_FILE_NAME

def save_channels_to_file():
    """通知チャンネル設定をファイルに保存する"""
    logger.info(f"Saving notification channel settings to file '{CHANNELS_FILE}'.")
    try:
        with open(CHANNELS_FILE, "w") as f:
            json.dump(_server_notification_channels, f)
        logger.debug("Notification channel settings saved successfully.")
    except Exception as e:
        logger.error(f"An error occurred while saving notification channel settings to file '{CHANNELS_FILE}': {e}")


def load_channels_from_file():
    """ファイルから通知チャンネル設定を読み込む"""
    logger.info(f"Loading notification channel settings from file '{CHANNELS_FILE}'.")
    global _server_notification_channels
    if os.path.exists(CHANNELS_FILE):
        try:
            with open(CHANNELS_FILE, "r") as f:
                content = f.read().strip()
                if content:
                    _server_notification_channels = json.loads(content)
                    logger.debug(f"Loaded notification channel settings from file '{CHANNELS_FILE}'.")
                else:
                    _server_notification_channels = {}
                    logger.debug(f"File '{CHANNELS_FILE}' was empty. Loading empty settings.")
        except json.JSONDecodeError:
            logger.error(f"Error: Failed to load {CHANNELS_FILE}. Invalid JSON format.")
            _server_notification_channels = {}
        except Exception as e:
            logger.error(f"An error occurred while loading notification channel settings from file '{CHANNELS_FILE}': {e}")
            _server_notification_channels = {}
    else:
        _server_notification_channels = {}
        logger.info(f"Notification channel settings file '{CHANNELS_FILE}' not found. Loading empty settings.")

    # JSONに保存されるキーは文字列になるため、読み込み後も辞書のキーを文字列に統一する
    _server_notification_channels = {str(guild_id): channel_id for guild_id, channel_id in _server_notification_channels.items()}
    logger.debug(f"Loaded notification channel settings: {_server_notification_channels}")


def get_notification_channel_id(guild_id: int):
    """指定されたギルドの通知チャンネルIDを取得する"""
    guild_id_str = str(guild_id)
    channel_id = _server_notification_channels.get(guild_id_str)
    logger.debug(f"Fetched notification channel ID for guild {guild_id}: {channel_id}")
    return channel_id

def set_notification_channel_id(guild_id: int, channel_id: int):
    """指定されたギルドの通知チャンネルIDを設定する"""
    guild_id_str = str(guild_id)
    _server_notification_channels[guild_id_str] = channel_id
    logger.info(f"Set notification channel ID for guild {guild_id} to {channel_id}.")
    save_channels_to_file()

# 初期ロード
load_channels_from_file()
