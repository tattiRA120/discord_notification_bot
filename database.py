import aiosqlite
import os
import logging # logging モジュールをインポート
import constants # constants モジュールをインポート

# ロガーを取得
logger = logging.getLogger(__name__)

# データベースファイル名
DB_FILE = constants.DB_FILE_NAME

async def init_db():
    logger.info(f"データベース '{DB_FILE}' の初期化を開始します。")
    # データベースファイルが存在しない場合にメッセージを出力
    if not os.path.exists(DB_FILE):
        logger.info(f"データベースファイル '{DB_FILE}' が見つかりませんでした。新規作成します。")

    try:
        conn = await aiosqlite.connect(DB_FILE)
        cursor = await conn.cursor()

        # sessions テーブル: 通話セッションの基本情報を記録 (月キー、開始時刻、期間)
        # id: セッションID (主キー、自動採番)
        # month_key: 年月 (YYYY-MM 形式)
        # start_time: セッション開始時刻 (ISO 8601 形式)
        # duration: セッション期間 (秒単位)
        await cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS {constants.TABLE_SESSIONS} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                {constants.COLUMN_MONTH_KEY} TEXT NOT NULL,
                {constants.COLUMN_START_TIME} TEXT NOT NULL,
                duration INTEGER NOT NULL
            )
        """)
        logger.debug(f"テーブル '{constants.TABLE_SESSIONS}' の存在確認または作成を実行しました。")

        # session_participants テーブル: 各セッションの参加メンバーを記録 (sessions テーブルへの外部キーあり)
        # session_id: セッションID (sessions テーブルの id を参照)
        # member_id: メンバーID
        # PRIMARY KEY (session_id, member_id): セッションとメンバーの組み合わせで一意
        # FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE: sessions のレコード削除時に連動して削除
        await cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS {constants.TABLE_SESSION_PARTICIPANTS} (
                {constants.COLUMN_SESSION_ID} INTEGER,
                {constants.COLUMN_MEMBER_ID} INTEGER NOT NULL,
                PRIMARY KEY ({constants.COLUMN_SESSION_ID}, {constants.COLUMN_MEMBER_ID}),
                FOREIGN KEY ({constants.COLUMN_SESSION_ID}) REFERENCES {constants.TABLE_SESSIONS}(id) ON DELETE CASCADE
            )
        """)
        logger.debug(f"テーブル '{constants.TABLE_SESSION_PARTICIPANTS}' の存在確認または作成を実行しました。")

        # member_monthly_stats テーブル: メンバーごとの月間累計通話時間を記録
        # month_key: 年月 (YYYY-MM 形式)
        # member_id: メンバーID
        # total_duration: 月間累計通話時間 (秒単位)
        # PRIMARY KEY (month_key, member_id): 年月とメンバーの組み合わせで一意
        await cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS {constants.TABLE_MEMBER_MONTHLY_STATS} (
                {constants.COLUMN_MONTH_KEY} TEXT NOT NULL,
                {constants.COLUMN_MEMBER_ID} INTEGER NOT NULL,
                {constants.COLUMN_TOTAL_DURATION} INTEGER NOT NULL DEFAULT {constants.DEFAULT_TOTAL_DURATION},
                PRIMARY KEY ({constants.COLUMN_MONTH_KEY}, {constants.COLUMN_MEMBER_ID})
            )
        """)
        logger.debug(f"テーブル '{constants.TABLE_MEMBER_MONTHLY_STATS}' の存在確認または作成を実行しました。")

        # settings テーブル: ギルドごとの設定情報を記録 (寝落ち確認のタイムアウト時間など)
        # guild_id: ギルドID (主キー)
        # lonely_timeout_minutes: 一人以下の状態が続く時間 (分単位)
        # reaction_wait_minutes: 寝落ち確認メッセージへの反応を待つ時間 (分単位)
        await cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS {constants.TABLE_SETTINGS} (
                {constants.COLUMN_GUILD_ID} TEXT PRIMARY KEY,
                {constants.COLUMN_LONELY_TIMEOUT_MINUTES} INTEGER DEFAULT {constants.DEFAULT_LONELY_TIMEOUT_MINUTES},
                {constants.COLUMN_REACTION_WAIT_MINUTES} INTEGER DEFAULT {constants.DEFAULT_REACTION_WAIT_MINUTES}
            )
        """)
        logger.debug(f"テーブル '{constants.TABLE_SETTINGS}' の存在確認または作成を実行しました。")

        # インデックスの作成 (クエリパフォーマンス向上のため)
        await cursor.execute(f"CREATE INDEX IF NOT EXISTS idx_sessions_month_key ON {constants.TABLE_SESSIONS} ({constants.COLUMN_MONTH_KEY})")
        await cursor.execute(f"CREATE INDEX IF NOT EXISTS idx_session_participants_session_id ON {constants.TABLE_SESSION_PARTICIPANTS} ({constants.COLUMN_SESSION_ID})")
        await cursor.execute(f"CREATE INDEX IF NOT EXISTS idx_session_participants_member_id ON {constants.TABLE_SESSION_PARTICIPANTS} ({constants.COLUMN_MEMBER_ID})")
        await cursor.execute(f"CREATE INDEX IF NOT EXISTS idx_member_monthly_stats_month_key ON {constants.TABLE_MEMBER_MONTHLY_STATS} ({constants.COLUMN_MONTH_KEY})")
        await cursor.execute(f"CREATE INDEX IF NOT EXISTS idx_member_monthly_stats_member_id ON {constants.TABLE_MEMBER_MONTHLY_STATS} ({constants.COLUMN_MEMBER_ID})")
        logger.debug("インデックスの存在確認または作成を実行しました。")

        await conn.commit()
        logger.debug("データベースの変更をコミットしました。")
    except Exception as e:
        logger.error(f"データベース初期化中にエラーが発生しました: {e}")
        raise # エラーを再送出
    finally:
        if conn:
            await conn.close()
            logger.debug("データベース接続を閉じました。")

    # データベースの初期化が完了したことを通知
    logger.info(f"データベース '{DB_FILE}' の初期化が完了しました。")


async def get_db_connection():
    """
    データベース接続を取得し、aiosqlite.Row ファクトリを設定します。
    """
    try:
        conn = await aiosqlite.connect(DB_FILE)
        conn.row_factory = aiosqlite.Row # カラム名でアクセスできるようにする
        logger.debug("データベース接続を取得しました。")
        return conn
    except Exception as e:
        logger.error(f"データベース接続の取得中にエラーが発生しました: {e}")
        raise # エラーを再送出

async def update_member_monthly_stats(month_key, member_id, duration):
    """
    指定された月のメンバーの累計通話時間を更新または挿入します。
    指定された月とメンバーの組み合わせが既に存在する場合は、total_duration を加算して更新します (ON CONFLICT)。
    存在しない場合は、新しいレコードを挿入します。
    """
    conn = None
    try:
        conn = await get_db_connection()
        cursor = await conn.cursor()
        await cursor.execute(SQL_UPSERT_MEMBER_MONTHLY_STATS, (month_key, member_id, duration))
        await conn.commit()
        logger.info(f"メンバー {member_id} の月間統計を更新しました (月: {month_key}, 期間: {duration})。")
    except Exception as e:
        logger.error(f"メンバー月間統計の更新中にエラーが発生しました (月: {month_key}, メンバーID: {member_id}, 期間: {duration}): {e}")
        # エラー発生時もロールバックは不要 (ON CONFLICT のため)
    finally:
        if conn:
            await conn.close()
            logger.debug("データベース接続を閉じました。")


async def record_voice_session_to_db(session_start, session_duration, participants):
    """
    通話セッションの情報をデータベースに記録します。
    sessions テーブルにセッション情報を挿入し、そのセッションに参加したメンバーを session_participants テーブルに挿入します。
    """
    conn = None
    try:
        conn = await get_db_connection()
        cursor = await conn.cursor()

        month_key = session_start.strftime("%Y-%m")
        start_time_iso = session_start.isoformat()

        # sessions テーブルにセッションを挿入
        await cursor.execute(SQL_INSERT_SESSION, (month_key, start_time_iso, session_duration))
        session_id = cursor.lastrowid # 挿入されたセッションのIDを取得
        logger.info(f"新しいセッションを記録しました。セッションID: {session_id}, 開始時刻: {start_time_iso}, 期間: {session_duration}")

        # session_participants テーブルに参加者を挿入
        if participants:
            participant_data = [(session_id, p) for p in participants]
            await cursor.executemany(SQL_INSERT_SESSION_PARTICIPANTS, participant_data)
            logger.debug(f"セッション {session_id} の参加者 {participants} を記録しました。")
        else:
            logger.debug(f"セッション {session_id} に参加者はいませんでした。")

        await conn.commit()
        logger.debug("データベースの変更をコミットしました。")
    except Exception as e:
        logger.error(f"通話セッションの記録中にエラーが発生しました (開始時刻: {session_start}, 期間: {session_duration}, 参加者: {participants}): {e}")
        if conn:
            await conn.rollback() # エラー発生時はロールバック
            logger.warning("データベースの変更をロールバックしました。")
    finally:
        if conn:
            await conn.close()
            logger.debug("データベース接続を閉じました。")


# SQL Queries
# メンバーの総通話時間を取得するクエリ
SQL_GET_TOTAL_CALL_TIME = f"""
    SELECT SUM({constants.COLUMN_TOTAL_DURATION}) as total
    FROM {constants.TABLE_MEMBER_MONTHLY_STATS}
    WHERE {constants.COLUMN_MEMBER_ID} = ?
"""

# ギルド設定を取得するクエリ
SQL_GET_GUILD_SETTINGS = f"SELECT * FROM {constants.TABLE_SETTINGS} WHERE {constants.COLUMN_GUILD_ID} = ?"

# settings テーブルへの挿入クエリ
SQL_UPSERT_SETTINGS_INSERT = f"INSERT INTO {constants.TABLE_SETTINGS} ({constants.COLUMN_GUILD_ID}, {constants.COLUMN_LONELY_TIMEOUT_MINUTES}, {constants.COLUMN_REACTION_WAIT_MINUTES}) VALUES (?, ?, ?)"
# settings テーブル更新時の SET 句 (lonely_timeout_minutes)
SQL_UPSERT_SETTINGS_UPDATE_SET_LONELY = f"{constants.COLUMN_LONELY_TIMEOUT_MINUTES} = ?"
# settings テーブル更新時の SET 句 (reaction_wait_minutes)
SQL_UPSERT_SETTINGS_UPDATE_SET_REACTION = f"{constants.COLUMN_REACTION_WAIT_MINUTES} = ?"
# settings テーブル更新時の ON CONFLICT 句
SQL_UPSERT_SETTINGS_ON_CONFLICT = f"ON CONFLICT({constants.COLUMN_GUILD_ID}) DO UPDATE SET "

# sessions テーブルへの挿入クエリ
SQL_INSERT_SESSION = f"""
    INSERT INTO {constants.TABLE_SESSIONS} ({constants.COLUMN_MONTH_KEY}, {constants.COLUMN_START_TIME}, duration)
    VALUES (?, ?, ?)
"""

# session_participants テーブルへの挿入クエリ
SQL_INSERT_SESSION_PARTICIPANTS = f"""
    INSERT INTO {constants.TABLE_SESSION_PARTICIPANTS} ({constants.COLUMN_SESSION_ID}, {constants.COLUMN_MEMBER_ID})
    VALUES (?, ?)
"""

# member_monthly_stats テーブルへの UPSERT (INSERT or UPDATE) クエリ
# 指定された月とメンバーの組み合わせが存在する場合は total_duration を加算して更新
SQL_UPSERT_MEMBER_MONTHLY_STATS = f"""
    INSERT INTO {constants.TABLE_MEMBER_MONTHLY_STATS} ({constants.COLUMN_MONTH_KEY}, {constants.COLUMN_MEMBER_ID}, {constants.COLUMN_TOTAL_DURATION})
    VALUES (?, ?, ?)
    ON CONFLICT({constants.COLUMN_MONTH_KEY}, {constants.COLUMN_MEMBER_ID}) DO UPDATE SET
    {constants.COLUMN_TOTAL_DURATION} = {constants.COLUMN_TOTAL_DURATION} + excluded.{constants.COLUMN_TOTAL_DURATION}
"""


async def get_total_call_time(member_id):
    """
    指定されたメンバーの総通話時間をデータベースから取得します。
    通話履歴がない場合はデフォルト値 (0) を返します。
    """
    conn = None
    try:
        conn = await get_db_connection()
        cursor = await conn.cursor()
        logger.debug(f"メンバー {member_id} の総通話時間を取得します。")
        await cursor.execute(SQL_GET_TOTAL_CALL_TIME, (member_id,))
        result = await cursor.fetchone()
        # 結果がNoneの場合（通話履歴がない場合）はデフォルト値 (0) を返す
        total_time = result['total'] if result and result['total'] is not None else constants.DEFAULT_TOTAL_DURATION
        logger.debug(f"メンバー {member_id} の総通話時間: {total_time}")
        return total_time
    except Exception as e:
        logger.error(f"メンバー {member_id} の総通話時間取得中にエラーが発生しました: {e}")
        return constants.DEFAULT_TOTAL_DURATION # エラー発生時はデフォルト値を返す
    finally:
        if conn:
            await conn.close()
            logger.debug("データベース接続を閉じました。")


async def get_guild_settings(guild_id):
    """
    指定されたギルドの設定情報をデータベースから取得します。
    設定が存在しない場合はデフォルト値を返します。
    """
    conn = None
    try:
        conn = await get_db_connection()
        cursor = await conn.cursor()
        logger.debug(f"ギルド {guild_id} の設定を取得します。")
        await cursor.execute(SQL_GET_GUILD_SETTINGS, (str(guild_id),))
        settings = await cursor.fetchone()
        if settings:
            logger.debug(f"ギルド {guild_id} の設定が見つかりました: {dict(settings)}")
            return settings
        else:
            logger.debug(f"ギルド {guild_id} の設定が見つかりませんでした。デフォルト値を返します。")
            # 設定がない場合はデフォルト値を返す (単位:分)
            return {
                constants.COLUMN_GUILD_ID: str(guild_id),
                constants.COLUMN_LONELY_TIMEOUT_MINUTES: constants.DEFAULT_LONELY_TIMEOUT_MINUTES,
                constants.COLUMN_REACTION_WAIT_MINUTES: constants.DEFAULT_REACTION_WAIT_MINUTES
            }
    except Exception as e:
        logger.error(f"ギルド {guild_id} の設定取得中にエラーが発生しました: {e}")
        # エラー発生時はデフォルト値を返す
        return {
            constants.COLUMN_GUILD_ID: str(guild_id),
            constants.COLUMN_LONELY_TIMEOUT_MINUTES: constants.DEFAULT_LONELY_TIMEOUT_MINUTES,
            constants.COLUMN_REACTION_WAIT_MINUTES: constants.DEFAULT_REACTION_WAIT_MINUTES
        }
    finally:
        if conn:
            await conn.close()
            logger.debug("データベース接続を閉じました。")


async def update_guild_settings(guild_id, lonely_timeout_minutes=None, reaction_wait_minutes=None):
    """
    指定されたギルドの設定情報を更新または挿入します (UPSERT)。
    設定が存在しない場合は新しいレコードを挿入し、存在する場合は指定された値を更新します。
    """
    conn = None
    try:
        conn = await get_db_connection()
        cursor = await conn.cursor()
        logger.info(f"ギルド {guild_id} の設定を更新します。lonely_timeout_minutes: {lonely_timeout_minutes}, reaction_wait_minutes: {reaction_wait_minutes}")

        # 現在の設定を取得して、更新されないパラメータのデフォルト値を決定
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

        logger.debug(f"実行するSQL: {update_sql}, パラメータ: {final_params}")
        await cursor.execute(update_sql, final_params)
        await conn.commit()
        logger.info(f"ギルド {guild_id} の設定を更新しました。")
    except Exception as e:
        logger.error(f"ギルド {guild_id} の設定更新中にエラーが発生しました: {e}")
        if conn:
            await conn.rollback() # エラー発生時はロールバック
            logger.warning("データベースの変更をロールバックしました。")
        raise # エラーを再送出
    finally:
        if conn:
            await conn.close()
            logger.debug("データベース接続を閉じました。")

async def close_db(conn):
    """
    データベース接続を閉じます。
    """
    if conn:
        try:
            await conn.close()
            logger.debug("データベース接続を閉じました。")
        except Exception as e:
            logger.error(f"データベース接続のクローズ中にエラーが発生しました: {e}")
