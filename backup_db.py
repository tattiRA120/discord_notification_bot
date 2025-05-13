import sqlite3
import os
import datetime
import logging
import constants

# ロガーを取得
logger = logging.getLogger(__name__)

DB_FILE = constants.DB_FILE_NAME
BACKUP_DIR = constants.BACKUP_DIR_NAME

# 同期的なデータベース接続を管理するコンテキストマネージャー
class DatabaseConnection:
    def __init__(self, db_file):
        self.db_file = db_file
        self.conn = None

    def __enter__(self):
        self.conn = sqlite3.connect(self.db_file)
        logger.debug(f"Database connection obtained: {self.db_file}")
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        if self.conn:
            self.conn.close()
            logger.debug(f"Database connection closed: {self.db_file}")
        # 例外が発生した場合は、そのまま伝播させる (Falseを返す)
        return False

def backup_database():
    logger.info("Starting database backup.")
    if not os.path.exists(BACKUP_DIR):
        os.makedirs(BACKUP_DIR)
        logger.debug(f"Created backup directory '{BACKUP_DIR}'.")

    backup_file = os.path.join(BACKUP_DIR, f"{constants.BACKUP_FILE_PREFIX}{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}{constants.DB_FILE_EXTENSION}")
    logger.debug(f"Backup file name: {backup_file}")

    try:
        # コンテキストマネージャーを使用してデータベースに接続
        with DatabaseConnection(DB_FILE) as con:
            logger.debug(f"Connected to source database '{DB_FILE}'.")
            # バックアップデータベースに接続 (存在しない場合は作成される)
            with DatabaseConnection(backup_file) as bck:
                logger.debug(f"Connected to backup database '{backup_file}'.")

                # バックアップを実行
                # pages=0 は全てのページをバックアップすることを意味します
                con.backup(bck, pages=constants.SQLITE_BACKUP_ALL_PAGES)
                logger.debug("Executed database backup.")

        logger.info(f"Database backup completed: {backup_file}")

        # 古いバックアップファイルを削除
        logger.info("Starting deletion of old backup files.")
        backup_files = [os.path.join(BACKUP_DIR, f) for f in os.listdir(BACKUP_DIR) if f.startswith(constants.BACKUP_FILE_PREFIX) and f.endswith(constants.DB_FILE_EXTENSION)]
        backup_files.sort(key=lambda x: os.path.getmtime(x)) # 最終更新時間でソート
        logger.debug(f"Found {len(backup_files)} backup files.")

        # 最新のconstants.NUM_BACKUP_FILES_TO_KEEP個以外のファイルを削除
        if len(backup_files) > constants.NUM_BACKUP_FILES_TO_KEEP:
            old_files = backup_files[:-constants.NUM_BACKUP_FILES_TO_KEEP]
            logger.info(f"Found {len(old_files)} old files exceeding the retention count ({constants.NUM_BACKUP_FILES_TO_KEEP}).")
            for old_file in old_files:
                try:
                    os.remove(old_file)
                    logger.info(f"Deleted old backup file: {old_file}")
                except OSError as e:
                    logger.error(f"An error occurred while deleting old backup file: {e}")
        else:
            logger.debug("No old files exceeding the retention count were found.")

    except sqlite3.Error as e:
        logger.error(f"A SQLite error occurred during database backup: {e}")
    except Exception as e:
        logger.error(f"An unexpected error occurred during database backup: {e}")

    logger.info("Database backup process finished.")


if __name__ == "__main__":
    backup_database()
