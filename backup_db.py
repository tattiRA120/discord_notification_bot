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

        # 古いバックアップファイルを削除
        backup_files = [os.path.join(BACKUP_DIR, f) for f in os.listdir(BACKUP_DIR) if f.startswith("voice_stats_") and f.endswith(".db")]
        backup_files.sort(key=lambda x: os.path.getmtime(x)) # 最終更新時間でソート

        # 最新の7個以外のファイルを削除
        if len(backup_files) > 7:
            old_files = backup_files[:-7]
            for old_file in old_files:
                try:
                    os.remove(old_file)
                    print(f"古いバックアップファイルを削除しました: {old_file}")
                except OSError as e:
                    print(f"古いバックアップファイルの削除中にエラーが発生しました: {e}")

    except sqlite3.Error as e:
        print(f"データベースのバックアップ中にエラーが発生しました: {e}")
    except Exception as e:
        print(f"予期しないエラーが発生しました: {e}")

if __name__ == "__main__":
    backup_database()
