import sqlite3
import shutil
import os
import datetime

DB_FILE = "voice_stats.db"
BACKUP_DIR = "backups"

def backup_database():
    if not os.path.exists(BACKUP_DIR):
        os.makedirs(BACKUP_DIR)

    backup_file = os.path.join(BACKUP_DIR, f"voice_stats_{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}.db")

    try:
        # データベースに接続
        con = sqlite3.connect(DB_FILE)
        # バックアップデータベースに接続 (存在しない場合は作成される)
        bck = sqlite3.connect(backup_file)

        # バックアップを実行
        # pages=0 は全てのページをバックアップすることを意味します
        con.backup(bck, pages=0)

        bck.close()
        con.close()
        print(f"データベースのバックアップが完了しました: {backup_file}")

    except sqlite3.Error as e:
        print(f"データベースのバックアップ中にエラーが発生しました: {e}")
    except Exception as e:
        print(f"予期しないエラーが発生しました: {e}")

if __name__ == "__main__":
    backup_database()
