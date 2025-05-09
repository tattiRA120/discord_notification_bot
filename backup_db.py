import sqlite3
import shutil
import os
import datetime
import logging # logging モジュールをインポート
from . import constants # constants モジュールをインポート

# ロガーを取得
logger = logging.getLogger(__name__)

DB_FILE = constants.DB_FILE_NAME
BACKUP_DIR = constants.BACKUP_DIR_NAME

def backup_database():
    logger.info("データベースのバックアップを開始します。")
    if not os.path.exists(BACKUP_DIR):
        os.makedirs(BACKUP_DIR)
        logger.debug(f"バックアップディレクトリ '{BACKUP_DIR}' を作成しました。")

    backup_file = os.path.join(BACKUP_DIR, f"{constants.BACKUP_FILE_PREFIX}{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}{constants.DB_FILE_EXTENSION}")
    logger.debug(f"バックアップファイル名: {backup_file}")

    con = None
    bck = None
    try:
        # データベースに接続
        con = sqlite3.connect(DB_FILE)
        logger.debug(f"データベース '{DB_FILE}' に接続しました。")
        # バックアップデータベースに接続 (存在しない場合は作成される)
        bck = sqlite3.connect(backup_file)
        logger.debug(f"バックアップデータベース '{backup_file}' に接続しました。")

        # バックアップを実行
        # pages=0 は全てのページをバックアップすることを意味します
        con.backup(bck, pages=constants.SQLITE_BACKUP_ALL_PAGES)
        logger.debug("データベースのバックアップを実行しました。")

        bck.close()
        con.close()
        logger.info(f"データベースのバックアップが完了しました: {backup_file}")

        # 古いバックアップファイルを削除
        logger.info("古いバックアップファイルの削除を開始します。")
        backup_files = [os.path.join(BACKUP_DIR, f) for f in os.listdir(BACKUP_DIR) if f.startswith(constants.BACKUP_FILE_PREFIX) and f.endswith(constants.DB_FILE_EXTENSION)]
        backup_files.sort(key=lambda x: os.path.getmtime(x)) # 最終更新時間でソート
        logger.debug(f"見つかったバックアップファイル数: {len(backup_files)}")

        # 最新のconstants.NUM_BACKUP_FILES_TO_KEEP個以外のファイルを削除
        if len(backup_files) > constants.NUM_BACKUP_FILES_TO_KEEP:
            old_files = backup_files[:-constants.NUM_BACKUP_FILES_TO_KEEP]
            logger.info(f"保持数 ({constants.NUM_BACKUP_FILES_TO_KEEP}) を超える古いファイルが {len(old_files)} 個見つかりました。")
            for old_file in old_files:
                try:
                    os.remove(old_file)
                    logger.info(f"古いバックアップファイルを削除しました: {old_file}")
                except OSError as e:
                    logger.error(f"古いバックアップファイルの削除中にエラーが発生しました: {e}")
        else:
            logger.debug("保持数を超える古いファイルはありませんでした。")

    except sqlite3.Error as e:
        logger.error(f"データベースのバックアップ中にSQLiteエラーが発生しました: {e}")
    except Exception as e:
        logger.error(f"データベースのバックアップ中に予期しないエラーが発生しました: {e}")
    finally:
        if bck:
            try:
                bck.close()
                logger.debug("バックアップデータベース接続を閉じました。")
            except Exception as e:
                logger.error(f"バックアップデータベース接続のクローズ中にエラーが発生しました: {e}")
        if con:
            try:
                con.close()
                logger.debug("元データベース接続を閉じました。")
            except Exception as e:
                logger.error(f"元データベース接続のクローズ中にエラーが発生しました: {e}")
    logger.info("データベースのバックアップ処理が終了しました。")


if __name__ == "__main__":
    backup_database()
