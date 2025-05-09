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
    logger.info(f"通知チャンネル設定をファイル '{CHANNELS_FILE}' に保存します。")
    try:
        with open(CHANNELS_FILE, "w") as f:
            json.dump(_server_notification_channels, f)
        logger.debug("通知チャンネル設定の保存が完了しました。")
    except Exception as e:
        logger.error(f"通知チャンネル設定のファイル '{CHANNELS_FILE}' への保存中にエラーが発生しました: {e}")


def load_channels_from_file():
    """ファイルから通知チャンネル設定を読み込む"""
    logger.info(f"通知チャンネル設定をファイル '{CHANNELS_FILE}' から読み込みます。")
    global _server_notification_channels
    if os.path.exists(CHANNELS_FILE):
        try:
            with open(CHANNELS_FILE, "r") as f:
                content = f.read().strip()
                if content:
                    _server_notification_channels = json.loads(content)
                    logger.debug(f"ファイル '{CHANNELS_FILE}' から通知チャンネル設定を読み込みました。")
                else:
                    _server_notification_channels = {}
                    logger.debug(f"ファイル '{CHANNELS_FILE}' は空でした。空の設定をロードします。")
        except json.JSONDecodeError:
            logger.error(f"エラー: {CHANNELS_FILE} の読み込みに失敗しました。JSON形式が不正です。")
            _server_notification_channels = {}
        except Exception as e:
            logger.error(f"通知チャンネル設定のファイル '{CHANNELS_FILE}' からの読み込み中にエラーが発生しました: {e}")
            _server_notification_channels = {}
    else:
        _server_notification_channels = {}
        logger.info(f"通知チャンネル設定ファイル '{CHANNELS_FILE}' が見つかりませんでした。空の設定をロードします。")

    # JSONに保存されるキーは文字列になるため、読み込み後も辞書のキーを文字列に統一する
    _server_notification_channels = {str(guild_id): channel_id for guild_id, channel_id in _server_notification_channels.items()}
    logger.debug(f"ロードされた通知チャンネル設定: {_server_notification_channels}")


def get_notification_channel_id(guild_id: int):
    """指定されたギルドの通知チャンネルIDを取得する"""
    guild_id_str = str(guild_id)
    channel_id = _server_notification_channels.get(guild_id_str)
    logger.debug(f"ギルド {guild_id} の通知チャンネルIDを取得しました: {channel_id}")
    return channel_id

def set_notification_channel_id(guild_id: int, channel_id: int):
    """指定されたギルドの通知チャンネルIDを設定する"""
    guild_id_str = str(guild_id)
    _server_notification_channels[guild_id_str] = channel_id
    logger.info(f"ギルド {guild_id} の通知チャンネルIDを {channel_id} に設定しました。")
    save_channels_to_file()

# 初期ロード
load_channels_from_file()
