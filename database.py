import aiosqlite
import os
import logging
import constants

# ロガーを取得
logger = logging.getLogger(__name__)

# データベース接続を管理する非同期コンテキストマネージャー
class DatabaseConnection:
    def __init__(self):
        self.conn = None

    async def __aenter__(self):
        self.conn = await aiosqlite.connect(DB_FILE)
        self.conn.row_factory = aiosqlite.Row # カラム名でアクセスできるようにする
        logger.debug("Database connection obtained.")
        return self.conn

    async def __aexit__(self, exc_type, exc, tb):
        if self.conn:
            await self.conn.close()
            logger.debug("Database connection closed.")
        # 例外が発生した場合は、そのまま伝播させる (Noneを返さない)
        return False


# データベースファイル名
DB_FILE = constants.DB_FILE_NAME

async def init_db():
    logger.info(f"Starting database '{DB_FILE}' initialization.")
    # データベースファイルが存在しない場合にメッセージを出力
    if not os.path.exists(DB_FILE):
        logger.info(f"Database file '{DB_FILE}' not found. Creating a new one.")

    try:
        conn = await aiosqlite.connect(DB_FILE)
        cursor = await conn.cursor()

        # sessions テーブル: 通話セッションの基本情報を記録 (月キー、開始時刻、期間)
        # id: セッションID (主キー、自動採番)
        # month_key: 年月 (YYYY-MM 形式)
        # start_time: セッション開始時刻 (ISO 8601 形式)
        # duration: セッション期間 (秒単位)
        # TODO: SQLクエリ構築の代替手段を検討 - f-stringを使用しているが、テーブル名/カラム名は定数由来のため直接的なSQLインジェクションリスクは低い。
        # より構造的なクエリビルダやライブラリの利用も検討可能。
        await cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS {constants.TABLE_SESSIONS} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                {constants.COLUMN_MONTH_KEY} TEXT NOT NULL,
                {constants.COLUMN_START_TIME} TEXT NOT NULL,
                duration INTEGER NOT NULL
            )
        """)
        logger.debug(f"Checked or created table '{constants.TABLE_SESSIONS}'.")

        # session_participants テーブル: 各セッションの参加メンバーを記録 (sessions テーブルへの外部キーあり)
        # session_id: セッションID (sessions テーブルの id を参照)
        # member_id: メンバーID
        # PRIMARY KEY (session_id, member_id): セッションとメンバーの組み合わせで一意
        # FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE: sessions のレコード削除時に連動して削除
        # TODO: SQLクエリ構築の代替手段を検討 - f-stringを使用しているが、テーブル名/カラム名は定数由来のため直接的なSQLインジェクションリスクは低い。
        # より構造的なクエリビルダやライブラリの利用も検討可能。
        await cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS {constants.TABLE_SESSION_PARTICIPANTS} (
                {constants.COLUMN_SESSION_ID} INTEGER,
                {constants.COLUMN_MEMBER_ID} INTEGER NOT NULL,
                PRIMARY KEY ({constants.COLUMN_SESSION_ID}, {constants.COLUMN_MEMBER_ID}),
                FOREIGN KEY ({constants.COLUMN_SESSION_ID}) REFERENCES {constants.TABLE_SESSIONS}(id) ON DELETE CASCADE
            )
        """)
        logger.debug(f"Checked or created table '{constants.TABLE_SESSION_PARTICIPANTS}'.")

        # member_monthly_stats テーブル: メンバーごとの月間累計通話時間を記録
        # month_key: 年月 (YYYY-MM 形式)
        # member_id: メンバーID
        # total_duration: 月間累計通話時間 (秒単位)
        # PRIMARY KEY (month_key, member_id): 年月とメンバーの組み合わせで一意
        # TODO: SQLクエリ構築の代替手段を検討 - f-stringを使用しているが、テーブル名/カラム名は定数由来のため直接的なSQLインジェクションリスクは低い。
        # より構造的なクエリビルダやライブラリの利用も検討可能。
        await cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS {constants.TABLE_MEMBER_MONTHLY_STATS} (
                {constants.COLUMN_MONTH_KEY} TEXT NOT NULL,
                {constants.COLUMN_MEMBER_ID} INTEGER NOT NULL,
                {constants.COLUMN_TOTAL_DURATION} INTEGER NOT NULL DEFAULT {constants.DEFAULT_TOTAL_DURATION},
                PRIMARY KEY ({constants.COLUMN_MONTH_KEY}, {constants.COLUMN_MEMBER_ID})
            )
        """)
        logger.debug(f"Checked or created table '{constants.TABLE_MEMBER_MONTHLY_STATS}'.")

        # settings テーブル: ギルドごとの設定情報を記録 (寝落ち確認のタイムアウト時間など)
        # guild_id: ギルドID (主キー)
        # lonely_timeout_minutes: 一人以下の状態が続く時間 (分単位)
        # reaction_wait_minutes: 寝落ち確認メッセージへの反応を待つ時間 (分単位)
        # TODO: SQLクエリ構築の代替手段を検討 - f-stringを使用しているが、テーブル名/カラム名は定数由来のため直接的なSQLインジェクションリスクは低い。
        # より構造的なクエリビルダやライブラリの利用も検討可能。
        await cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS {constants.TABLE_SETTINGS} (
                {constants.COLUMN_GUILD_ID} TEXT PRIMARY KEY,
                {constants.COLUMN_LONELY_TIMEOUT_MINUTES} INTEGER DEFAULT {constants.DEFAULT_LONELY_TIMEOUT_MINUTES},
                {constants.COLUMN_REACTION_WAIT_MINUTES} INTEGER DEFAULT {constants.DEFAULT_REACTION_WAIT_MINUTES}
            )
        """)
        logger.debug(f"Checked or created table '{constants.TABLE_SETTINGS}'.")

        # user_mute_stats テーブル: ユーザーごとのミュート回数を記録
        # user_id: ユーザーID (主キー)
        # mute_count: ミュート回数 (デフォルト0)
        await cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS {constants.TABLE_USER_MUTE_STATS} (
                {constants.COLUMN_MEMBER_ID} INTEGER PRIMARY KEY,
                {constants.COLUMN_MUTE_COUNT} INTEGER NOT NULL DEFAULT 0
            )
        """)
        logger.debug(f"Checked or created table '{constants.TABLE_USER_MUTE_STATS}'.")

        # インデックスの作成 (クエリパフォーマンス向上のため)
        # TODO: SQLクエリ構築の代替手段を検討 - f-stringを使用しているが、テーブル名/カラム名は定数由来のため直接的なSQLインジェクションリスクは低い。
        # より構造的なクエリビルダやライブラリの利用も検討可能。
        await cursor.execute(f"CREATE INDEX IF NOT EXISTS idx_sessions_month_key ON {constants.TABLE_SESSIONS} ({constants.COLUMN_MONTH_KEY})")
        # TODO: SQLクエリ構築の代替手段を検討 - f-stringを使用しているが、テーブル名/カラム名は定数由来のため直接的なSQLインジェクションリスクは低い。
        # より構造的なクエリビルダやライブラリの利用も検討可能。
        await cursor.execute(f"CREATE INDEX IF NOT EXISTS idx_session_participants_session_id ON {constants.TABLE_SESSION_PARTICIPANTS} ({constants.COLUMN_SESSION_ID})")
        # TODO: SQLクエリ構築の代替手段を検討 - f-stringを使用しているが、テーブル名/カラム名は定数由来のため直接的なSQLインジェクションリスクは低い。
        # より構造的なクエリビルダやライブラリの利用も検討可能。
        await cursor.execute(f"CREATE INDEX IF NOT EXISTS idx_session_participants_member_id ON {constants.TABLE_SESSION_PARTICIPANTS} ({constants.COLUMN_MEMBER_ID})")
        # TODO: SQLクエリ構築の代替手段を検討 - f-stringを使用しているが、テーブル名/カラム名は定数由来のため直接的なSQLインジェクションリスクは低い。
        # より構造的なクエリビルダやライブラリの利用も検討可能。
        await cursor.execute(f"CREATE INDEX IF NOT EXISTS idx_member_monthly_stats_month_key ON {constants.TABLE_MEMBER_MONTHLY_STATS} ({constants.COLUMN_MONTH_KEY})")
        # TODO: SQLクエリ構築の代替手段を検討 - f-stringを使用しているが、テーブル名/カラム名は定数由来のため直接的なSQLインジェクションリスクは低い。
        # より構造的なクエリビルダやライブラリの利用も検討可能。
        await cursor.execute(f"CREATE INDEX IF NOT EXISTS idx_member_monthly_stats_member_id ON {constants.TABLE_MEMBER_MONTHLY_STATS} ({constants.COLUMN_MEMBER_ID})")
        # user_mute_stats テーブルのインデックス (member_id は主キーなので自動的にインデックスが作成される)
        logger.debug("Checked or created indexes.")

        await conn.commit()
        logger.debug("Committed database changes.")
    except Exception as e:
        logger.error(f"An error occurred during database initialization: {e}")
        raise # エラーを再送出
    finally:
        if conn:
            await conn.close()
            logger.debug("Database connection closed.")

    # データベースの初期化が完了したことを通知
    logger.info(f"Database '{DB_FILE}' initialization complete.")


async def get_db_connection():
    """
    データベース接続を取得し、aiosqlite.Row ファクトリを設定します。
    """
    try:
        conn = await aiosqlite.connect(DB_FILE)
        conn.row_factory = aiosqlite.Row # カラム名でアクセスできるようにする
        logger.debug("Database connection obtained.")
        return conn
    except Exception as e:
        logger.error(f"An error occurred while getting database connection: {e}")
        raise # エラーを再送出

async def update_member_monthly_stats(month_key, member_id, duration):
    """
    指定された月のメンバーの累計通話時間を更新または挿入します。
    指定された月とメンバーの組み合わせが既に存在する場合は、total_duration を加算して更新します (ON CONFLICT)。
    存在しない場合は、新しいレコードを挿入します。
    """
    try:
        async with DatabaseConnection() as conn:
            cursor = await conn.cursor()
            await cursor.execute(SQL_UPSERT_MEMBER_MONTHLY_STATS, (month_key, member_id, duration))
            await conn.commit()
            # 更新後の total_duration を取得して返す
            await cursor.execute("SELECT total_duration FROM member_monthly_stats WHERE month_key = ? AND member_id = ?", (month_key, member_id))
            result = await cursor.fetchone()
            updated_total_duration = result['total_duration'] if result else constants.DEFAULT_TOTAL_DURATION
            logger.info(f"Updated monthly stats for member {member_id} (Month: {month_key}, Duration: {duration}). New total: {updated_total_duration}")
            return updated_total_duration
    except Exception as e:
        logger.error(f"An error occurred while updating member monthly stats (Month: {month_key}, Member ID: {member_id}, Duration: {duration}): {e}")
        # エラー発生時もロールバックは不要 (ON CONFLICT のため)
        return constants.DEFAULT_TOTAL_DURATION # エラー時はデフォルト値を返す


async def record_voice_session_to_db(session_start, session_duration, participants):
    """
    通話セッションの情報をデータベースに記録します。
    sessions テーブルにセッション情報を挿入し、そのセッションに参加したメンバーを session_participants テーブルに挿入します。
    """
    try:
        async with DatabaseConnection() as conn:
            cursor = await conn.cursor()

            month_key = session_start.strftime("%Y-%m")
            start_time_iso = session_start.isoformat()

            # sessions テーブルにセッションを挿入
            await cursor.execute(SQL_INSERT_SESSION, (month_key, start_time_iso, session_duration))
            session_id = cursor.lastrowid # 挿入されたセッションのIDを取得
            logger.info(f"Recorded new session. Session ID: {session_id}, Start time: {start_time_iso}, Duration: {session_duration}")

            # session_participants テーブルに参加者を挿入
            if participants:
                participant_data = [(session_id, p) for p in participants]
                await cursor.executemany(SQL_INSERT_SESSION_PARTICIPANTS, participant_data)
                logger.debug(f"Recorded participants {participants} for session {session_id}.")
            else:
                logger.debug(f"No participants in session {session_id}.")

            await conn.commit()
            logger.debug("Committed database changes.")
    except Exception as e:
        logger.error(f"An error occurred while recording voice session (Start time: {session_start}, Duration: {session_duration}, Participants: {participants}): {e}")
        # aiosqliteのwith構文はデフォルトで例外発生時にロールバックしないため、必要に応じてtry/except内でrollback()を呼び出す
        try:
            async with DatabaseConnection() as conn_rollback:
                 await conn_rollback.rollback()
                 logger.warning("Rolled back database changes.")
        except Exception as rollback_e:
             logger.error(f"An error occurred during rollback: {rollback_e}")
        raise # エラーを再送出


# SQL Queries
# メンバーの総通話時間を取得するクエリ
# TODO: SQLクエリ構築の代替手段を検討 - f-stringを使用しているが、テーブル名/カラム名は定数由来のため直接的なSQLインジェクションリスクは低い。
# より構造的なクエリビルダやライブラリの利用も検討可能。
SQL_GET_TOTAL_CALL_TIME = f"""
    SELECT SUM({constants.COLUMN_TOTAL_DURATION}) as total
    FROM {constants.TABLE_MEMBER_MONTHLY_STATS}
    WHERE {constants.COLUMN_MEMBER_ID} = ?
"""

# ギルド設定を取得するクエリ
# TODO: SQLクエリ構築の代替手段を検討 - f-stringを使用しているが、テーブル名/カラム名は定数由来のため直接的なSQLインジェクションリスクは低い。
# より構造的なクエリビルダやライブラリの利用も検討可能。
SQL_GET_GUILD_SETTINGS = f"SELECT * FROM {constants.TABLE_SETTINGS} WHERE {constants.COLUMN_GUILD_ID} = ?"

# settings テーブルへの挿入クエリ
# TODO: SQLクエリ構築の代替手段を検討 - f-stringを使用しているが、テーブル名/カラム名は定数由来のため直接的なSQLインジェクションリスクは低い。
# より構造的なクエリビルダやライブラリの利用も検討可能。
SQL_UPSERT_SETTINGS_INSERT = f"INSERT INTO {constants.TABLE_SETTINGS} ({constants.COLUMN_GUILD_ID}, {constants.COLUMN_LONELY_TIMEOUT_MINUTES}, {constants.COLUMN_REACTION_WAIT_MINUTES}) VALUES (?, ?, ?)"
# settings テーブル更新時の SET 句 (lonely_timeout_minutes)
# TODO: SQLクエリ構築の代替手段を検討 - f-stringを使用しているが、テーブル名/カラム名は定数由来のため直接的なSQLインジェクションリスクは低い。
# より構造的なクエリビルダやライブラリの利用も検討可能。
SQL_UPSERT_SETTINGS_UPDATE_SET_LONELY = f"{constants.COLUMN_LONELY_TIMEOUT_MINUTES} = ?"
# settings テーブル更新時の SET 句 (reaction_wait_minutes)
# TODO: SQLクエリ構築の代替手段を検討 - f-stringを使用しているが、テーブル名/カラム名は定数由来のため直接的なSQLインジェクションリスクは低い。
# より構造的なクエリビルダやライブラリの利用も検討可能。
SQL_UPSERT_SETTINGS_UPDATE_SET_REACTION = f"{constants.COLUMN_REACTION_WAIT_MINUTES} = ?"
# settings テーブル更新時の ON CONFLICT 句
# TODO: SQLクエリ構築の代替手段を検討 - f-stringを使用しているが、テーブル名/カラム名は定数由来のため直接的なSQLインジェクションリスクは低い。
# より構造的なクエリビルダやライブラリの利用も検討可能。
SQL_UPSERT_SETTINGS_ON_CONFLICT = f"ON CONFLICT({constants.COLUMN_GUILD_ID}) DO UPDATE SET "

# sessions テーブルへの挿入クエリ
# TODO: SQLクエリ構築の代替手段を検討 - f-stringを使用しているが、テーブル名/カラム名は定数由来のため直接的なSQLインジェクションリスクは低い。
# より構造的なクエリビルダやライブラリの利用も検討可能。
SQL_INSERT_SESSION = f"""
    INSERT INTO {constants.TABLE_SESSIONS} ({constants.COLUMN_MONTH_KEY}, {constants.COLUMN_START_TIME}, duration)
    VALUES (?, ?, ?)
"""

# session_participants テーブルへの挿入クエリ
# TODO: SQLクエリ構築の代替手段を検討 - f-stringを使用しているが、テーブル名/カラム名は定数由来のため直接的なSQLインジェクションリスクは低い。
# より構造的なクエリビルダやライブラリの利用も検討可能。
SQL_INSERT_SESSION_PARTICIPANTS = f"""
    INSERT INTO {constants.TABLE_SESSION_PARTICIPANTS} ({constants.COLUMN_SESSION_ID}, {constants.COLUMN_MEMBER_ID})
    VALUES (?, ?)
"""

# member_monthly_stats テーブルへの UPSERT (INSERT or UPDATE) クエリ
# 指定された月とメンバーの組み合わせが存在する場合は total_duration を加算して更新
# TODO: SQLクエリ構築の代替手段を検討 - f-stringを使用しているが、テーブル名/カラム名は定数由来のため直接的なSQLインジェクションリスクは低い。
# より構造的なクエリビルダやライブラリの利用も検討可能。
SQL_UPSERT_MEMBER_MONTHLY_STATS = f"""
    INSERT INTO {constants.TABLE_MEMBER_MONTHLY_STATS} ({constants.COLUMN_MONTH_KEY}, {constants.COLUMN_MEMBER_ID}, {constants.COLUMN_TOTAL_DURATION})
    VALUES (?, ?, ?)
    ON CONFLICT({constants.COLUMN_MONTH_KEY}, {constants.COLUMN_MEMBER_ID}) DO UPDATE SET
    {constants.COLUMN_TOTAL_DURATION} = {constants.COLUMN_TOTAL_DURATION} + excluded.{constants.COLUMN_TOTAL_DURATION}
"""

# user_mute_stats テーブルへの UPSERT (INSERT or UPDATE) クエリ
# 指定されたユーザーIDが存在する場合は mute_count をインクリメントし、存在しない場合は新しいレコードを挿入
SQL_UPSERT_MUTE_COUNT = f"""
    INSERT INTO {constants.TABLE_USER_MUTE_STATS} ({constants.COLUMN_MEMBER_ID}, {constants.COLUMN_MUTE_COUNT})
    VALUES (?, 1)
    ON CONFLICT({constants.COLUMN_MEMBER_ID}) DO UPDATE SET
    {constants.COLUMN_MUTE_COUNT} = {constants.COLUMN_MUTE_COUNT} + 1
"""

# user_mute_stats テーブルから指定されたユーザーIDのミュート回数を取得するクエリ
SQL_GET_MUTE_COUNT = f"""
    SELECT {constants.COLUMN_MUTE_COUNT}
    FROM {constants.TABLE_USER_MUTE_STATS}
    WHERE {constants.COLUMN_MEMBER_ID} = ?
"""


async def increment_mute_count(user_id: int):
    """
    指定されたユーザーのミュート回数をインクリメントします。
    ユーザーが存在しない場合は、新しいレコードを作成します。
    """
    try:
        async with DatabaseConnection() as conn:
            cursor = await conn.cursor()
            await cursor.execute(SQL_UPSERT_MUTE_COUNT, (user_id,))
            await conn.commit()
            logger.info(f"Incremented mute count for user {user_id}. Changes committed.")
    except Exception as e:
        logger.error(f"An error occurred while incrementing mute count for user {user_id}: {e}")
        # エラー発生時もロールバックは不要 (ON CONFLICT のため)
        # 必要に応じてエラーを再送出するか、特定の値を返す
        raise


async def get_mute_count(user_id: int) -> int:
    """
    指定されたユーザーのミュート回数を取得します。
    ユーザーが存在しない場合は0を返します。
    """
    try:
        async with DatabaseConnection() as conn:
            cursor = await conn.cursor()
            await cursor.execute(SQL_GET_MUTE_COUNT, (user_id,))
            result = await cursor.fetchone()
            if result:
                mute_count = result[constants.COLUMN_MUTE_COUNT]
                logger.info(f"Fetched mute count for user {user_id}: {mute_count}")
                return mute_count
            else:
                logger.info(f"No mute count record found for user {user_id}. Returning 0.")
                return 0
    except Exception as e:
        logger.error(f"An error occurred while fetching mute count for user {user_id}: {e}")
        return 0 # エラー発生時は0を返す


async def get_participants_by_session_ids(session_ids: list):
    """
    指定されたセッションIDリストに含まれるセッションの参加者を取得し、セッションIDごとにグループ化して返します。
    """
    if not session_ids:
        return {}

    try:
        async with DatabaseConnection() as conn:
            cursor = await conn.cursor()

            # 全セッションの参加者を一度に取得
            placeholders = ','.join('?' for _ in session_ids)
            # TODO: SQLクエリ構築の代替手段を検討 - f-stringを使用しているが、テーブル名/カラム名は定数由来のため直接的なSQLインジェクションリスクは低い。
            # より構造的なクエリビルダやライブラリの利用も検討可能。
            await cursor.execute(f"""
                SELECT session_id, member_id FROM session_participants
                WHERE session_id IN ({placeholders})
            """, session_ids)
            all_participants_data = await cursor.fetchall()

            # セッションIDごとに参加者をグループ化
            session_participants_map = {}
            for participant_row in all_participants_data:
                session_id = participant_row['session_id']
                member_id = participant_row['member_id']
                if session_id not in session_participants_map:
                    session_participants_map[session_id] = []
                session_participants_map[session_id].append(member_id)

            logger.debug(f"Fetched participants for {len(session_ids)} sessions.")
            return session_participants_map
    except Exception as e:
        logger.error(f"An error occurred while fetching participants by session IDs: {e}")
        return {} # エラー発生時は空の辞書を返す


async def get_total_call_time(member_id):
    """
    指定されたメンバーの総通話時間をデータベースから取得します。
    通話履歴がない場合はデフォルト値 (0) を返します。
    """
    try:
        async with DatabaseConnection() as conn:
            cursor = await conn.cursor()
            logger.debug(f"Fetching total call time for member {member_id}.")
            await cursor.execute(SQL_GET_TOTAL_CALL_TIME, (member_id,))
            result = await cursor.fetchone()
            # 結果がNoneの場合（通話履歴がない場合）はデフォルト値 (0) を返す
            total_time = result['total'] if result and result['total'] is not None else constants.DEFAULT_TOTAL_DURATION
            logger.debug(f"Total call time for member {member_id}: {total_time}")
            return total_time
    except Exception as e:
        logger.error(f"An error occurred while fetching total call time for member {member_id}: {e}")
        return constants.DEFAULT_TOTAL_DURATION # エラー発生時はデフォルト値を返す

async def get_monthly_voice_sessions(month_key: str):
    """
    指定された月の全セッションと参加者を取得します。
    """
    try:
        async with DatabaseConnection() as conn:
            cursor = await conn.cursor()

            # 指定された月の全セッションを取得
            await cursor.execute("""
                SELECT start_time, duration, id FROM sessions
                WHERE month_key = ?
            """, (month_key,))
            sessions_data = await cursor.fetchall()
            logger.debug(f"Found {len(sessions_data)} sessions for month {month_key}")

            sessions = []
            session_ids = [session_row['id'] for session_row in sessions_data]

            # 取得したセッションに参加したメンバーをまとめて取得し、セッションIDごとにグループ化
            if session_ids:
                placeholders = ','.join('?' for _ in session_ids)
                await cursor.execute(f"""
                    SELECT session_id, member_id FROM session_participants
                    WHERE session_id IN ({placeholders})
                """, session_ids)
                all_participants_data = await cursor.fetchall()

                session_participants_map = {}
                for participant_row in all_participants_data:
                    session_id = participant_row['session_id']
                    member_id = participant_row['member_id']
                    if session_id not in session_participants_map:
                        session_participants_map[session_id] = []
                    session_participants_map[session_id].append(member_id)

                # セッションデータにメンバー情報を結合
                for session_row in sessions_data:
                    sessions.append({
                        "id": session_row['id'],
                        "start_time": session_row['start_time'],
                        "duration": session_row['duration'],
                        "participants": session_participants_map.get(session_row['id'], [])
                    })
            logger.debug(f"Prepared {len(sessions)} sessions with participants for month {month_key}")
            return sessions
    except Exception as e:
        logger.error(f"An error occurred while fetching monthly voice sessions for month {month_key}: {e}")
        return [] # エラー発生時は空のリストを返す

async def get_monthly_member_stats(month_key: str):
    """
    指定された月のメンバー別累計通話時間を取得します。
    """
    try:
        async with DatabaseConnection() as conn:
            cursor = await conn.cursor()
            # 指定された月のメンバー別累計通話時間を取得
            await cursor.execute("""
                SELECT member_id, total_duration FROM member_monthly_stats
                WHERE month_key = ?
            """, (month_key,))
            member_stats_data = await cursor.fetchall()
            # メンバーIDをキーとした辞書に変換
            member_stats = {m['member_id']: m['total_duration'] for m in member_stats_data}
            logger.debug(f"Found stats for {len(member_stats)} members for month {month_key}")
            return member_stats
    except Exception as e:
        logger.error(f"An error occurred while fetching monthly member stats for month {month_key}: {e}")
        return {} # エラー発生時は空の辞書を返す

async def get_annual_voice_sessions(year: str):
    """
    指定された年度の全セッションと参加者を取得します。
    """
    try:
        async with DatabaseConnection() as conn:
            cursor = await conn.cursor()

            # 対象年度の全セッションを取得
            await cursor.execute("""
                SELECT start_time, duration, id FROM sessions
                WHERE strftime('%Y', start_time) = ?
            """, (year,))
            sessions_data = await cursor.fetchall()
            logger.debug(f"Found {len(sessions_data)} sessions for year {year}")

            sessions_all = []
            session_ids = [session_row['id'] for session_row in sessions_data]

            if session_ids:
                # 全セッションの参加者を一度に取得
                placeholders = ','.join('?' for _ in session_ids)
                await cursor.execute(f"""
                    SELECT session_id, member_id FROM session_participants
                    WHERE session_id IN ({placeholders})
                """, session_ids)
                all_participants_data = await cursor.fetchall()

                # セッションIDごとに参加者をグループ化
                session_participants_map = {}
                for participant_row in all_participants_data:
                    session_id = participant_row['session_id']
                    member_id = participant_row['member_id']
                    if session_id not in session_participants_map:
                        session_participants_map[session_id] = []
                    session_participants_map[session_id].append(member_id)

                # セッションデータにメンバー情報を結合
                for session_row in sessions_data:
                    sessions_all.append({
                        "id": session_row['id'],
                        "start_time": session_row['start_time'],
                        "duration": session_row['duration'],
                        "participants": session_participants_map.get(session_row['id'], [])
                    })
            logger.debug(f"Prepared {len(sessions_all)} sessions with participants for year {year}")
            return sessions_all
    except Exception as e:
        logger.error(f"An error occurred while fetching annual voice sessions for year {year}: {e}")
        return [] # エラー発生時は空のリストを返す

async def get_annual_member_total_stats(year: str):
    """
    指定された年度のメンバー別累計通話時間を取得します。
    """
    try:
        async with DatabaseConnection() as conn:
            cursor = await conn.cursor()
            # 対象年度のメンバー別累計時間を全て取得
            await cursor.execute("""
                SELECT member_id, SUM(total_duration) as total_duration
                FROM member_monthly_stats
                WHERE strftime('%Y', month_key) = ?
                GROUP BY member_id
            """, (year,))
            members_total_data = await cursor.fetchall()
            # メンバーIDをキーとした辞書に変換
            members_total = {m['member_id']: m['total_duration'] for m in members_total_data}
            logger.debug(f"Found stats for {len(members_total)} members for year {year}")
            return members_total
    except Exception as e:
        logger.error(f"An error occurred while fetching annual member total stats for year {year}: {e}")
        return {} # エラー発生時は空の辞書を返す

async def get_total_call_time_for_guild_members(member_ids: list):
    """
    指定されたメンバーIDリストに含まれるメンバーの総通話時間を取得します。
    """
    if not member_ids:
        return {}

    try:
        async with DatabaseConnection() as conn:
            cursor = await conn.cursor()
            logger.debug(f"Fetching total call time for {len(member_ids)} members.")

            # 指定されたメンバーIDの総通話時間をまとめて取得
            placeholders = ','.join('?' for _ in member_ids)
            # TODO: SQLクエリ構築の代替手段を検討 - f-stringを使用しているが、テーブル名/カラム名は定数由来のため直接的なSQLインジェクションリスクは低い。
            # より構造的なクエリビルダやライブラリの利用も検討可能。
            await cursor.execute(f"""
                SELECT member_id, SUM({constants.COLUMN_TOTAL_DURATION}) as total
                FROM {constants.TABLE_MEMBER_MONTHLY_STATS}
                WHERE {constants.COLUMN_MEMBER_ID} IN ({placeholders})
                GROUP BY {constants.COLUMN_MEMBER_ID}
            """, member_ids)
            results = await cursor.fetchall()

            # メンバーIDをキーとした辞書に変換
            member_call_times = {row['member_id']: row['total'] for row in results}

            # 通話時間が0のメンバーも結果に含める（辞書に存在しない場合は0とする）
            for member_id in member_ids:
                if member_id not in member_call_times:
                    member_call_times[member_id] = constants.DEFAULT_TOTAL_DURATION

            logger.debug(f"Fetched total call times for {len(member_call_times)} members.")
            return member_call_times
    except Exception as e:
        logger.error(f"An error occurred while fetching total call time for guild members: {e}")
        return {} # エラー発生時は空の辞書を返す


async def get_guild_settings(guild_id):
    """
    指定されたギルドの設定情報をデータベースから取得します。
    設定が存在しない場合はデフォルト値を返します。
    """
    try:
        async with DatabaseConnection() as conn:
            cursor = await conn.cursor()
            logger.debug(f"Fetching settings for guild {guild_id}.")
            await cursor.execute(SQL_GET_GUILD_SETTINGS, (str(guild_id),))
            settings = await cursor.fetchone()
            if settings:
                logger.debug(f"Settings found for guild {guild_id}: {dict(settings)}")
                return settings
            else:
                logger.debug(f"Settings not found for guild {guild_id}. Returning default values.")
                # 設定がない場合はデフォルト値を返す (単位:分)
                return {
                    constants.COLUMN_GUILD_ID: str(guild_id),
                    constants.COLUMN_LONELY_TIMEOUT_MINUTES: constants.DEFAULT_LONELY_TIMEOUT_MINUTES,
                    constants.COLUMN_REACTION_WAIT_MINUTES: constants.DEFAULT_REACTION_WAIT_MINUTES
                }
    except Exception as e:
        logger.error(f"An error occurred while fetching settings for guild {guild_id}: {e}")
        # エラー発生時はデフォルト値を返す
        return {
            constants.COLUMN_GUILD_ID: str(guild_id),
            constants.COLUMN_LONELY_TIMEOUT_MINUTES: constants.DEFAULT_LONELY_TIMEOUT_MINUTES,
            constants.COLUMN_REACTION_WAIT_MINUTES: constants.DEFAULT_REACTION_WAIT_MINUTES
        }


async def update_guild_settings(guild_id, lonely_timeout_minutes=None, reaction_wait_minutes=None):
    """
    指定されたギルドの設定情報を更新または挿入します (UPSERT)。
    設定が存在しない場合は新しいレコードを挿入し、存在する場合は指定された値を更新します。
    """
    try:
        async with DatabaseConnection() as conn:
            cursor = await conn.cursor()
            logger.info(f"Updating settings for guild {guild_id}. lonely_timeout_minutes: {lonely_timeout_minutes}, reaction_wait_minutes: {reaction_wait_minutes}")

            # 現在の設定を取得して、更新されないパラメータのデフォルト値を決定
            # update_guild_settings内で確立した接続をget_guild_settingsに渡すことで
            # データベース接続の効率化を目指したが、接続のライフサイクル管理の問題から
            # Connection closedエラーが発生した。
            # 今後の改善策として、get_guild_settingsが必須で接続を受け取るようにし、
            # 呼び出し元が接続の確立・クローズを責任持つようにするリファクタリングが必要。
            settings = await get_guild_settings(guild_id)

            set_clauses = []
            params = [str(guild_id)] # guild_id は ON CONFLICT のために最初に追加

            # INSERT 部分の VALUES (?, ?, ?) に対応するパラメータ
            insert_params = [str(guild_id)]

            if lonely_timeout_minutes is not None:
                set_clauses.append(SQL_UPSERT_SETTINGS_UPDATE_SET_LONELY)
                params.append(lonely_timeout_minutes)
                insert_params.append(lonely_timeout_minutes)
            else:
                insert_params.append(settings[constants.COLUMN_LONELY_TIMEOUT_MINUTES])

            if reaction_wait_minutes is not None:
                set_clauses.append(SQL_UPSERT_SETTINGS_UPDATE_SET_REACTION)
                params.append(reaction_wait_minutes)
                insert_params.append(reaction_wait_minutes)
            else:
                insert_params.append(settings[constants.COLUMN_REACTION_WAIT_MINUTES])

            # ON CONFLICT DO UPDATE SET の部分を構築
            update_sql = SQL_UPSERT_SETTINGS_INSERT + SQL_UPSERT_SETTINGS_ON_CONFLICT
            update_sql += ", ".join(set_clauses)

            # パラメータの順序を調整: INSERT のパラメータ + UPDATE のパラメータ
            final_params = insert_params + params[1:] # params[0] は guild_id で重複するため除外

            logger.debug(f"Executing SQL: {update_sql}, Parameters: {final_params}")
            await cursor.execute(update_sql, final_params)
            await conn.commit()
            logger.info(f"Settings updated for guild {guild_id}.")
    except Exception as e:
        logger.error(f"An error occurred while updating settings for guild {guild_id}: {e}")
        try:
            async with DatabaseConnection() as conn_rollback:
                 await conn_rollback.rollback()
                 logger.warning("Rolled back database changes.")
        except Exception as rollback_e:
             logger.error(f"An error occurred during rollback: {rollback_e}")
        raise # エラーを再送出

# close_db 関数は、DatabaseConnection コンテキストマネージャーや init_db 関数内で接続が閉じられるため、不要と判断し削除しました。
