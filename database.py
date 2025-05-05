import aiosqlite
import os

# データベースファイル名
DB_FILE = "voice_stats.db"

async def init_db():
    # データベースファイルが存在しない場合にメッセージを出力
    if not os.path.exists(DB_FILE):
        print(f"データベースファイル '{DB_FILE}' が見つかりませんでした。新規作成します。")

    conn = await aiosqlite.connect(DB_FILE)
    cursor = await conn.cursor()

    # sessions テーブル
    await cursor.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            month_key TEXT NOT NULL,
            start_time TEXT NOT NULL,
            duration INTEGER NOT NULL
        )
    """)

    # session_participants テーブル
    await cursor.execute("""
        CREATE TABLE IF NOT EXISTS session_participants (
            session_id INTEGER,
            member_id TEXT NOT NULL,
            PRIMARY KEY (session_id, member_id),
            FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
        )
    """)

    # member_monthly_stats テーブル
    await cursor.execute("""
        CREATE TABLE IF NOT EXISTS member_monthly_stats (
            month_key TEXT NOT NULL,
            member_id TEXT NOT NULL,
            total_duration INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (month_key, member_id)
        )
    """)

    # settings テーブル
    await cursor.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            guild_id TEXT PRIMARY KEY,
            lonely_timeout_minutes INTEGER DEFAULT 180, -- 3時間を分に変換
            reaction_wait_minutes INTEGER DEFAULT 5
        )
    """)

    # インデックス
    await cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_month_key ON sessions (month_key)")
    await cursor.execute("CREATE INDEX IF NOT EXISTS idx_session_participants_session_id ON session_participants (session_id)")
    await cursor.execute("CREATE INDEX IF NOT EXISTS idx_session_participants_member_id ON session_participants (member_id)")
    await cursor.execute("CREATE INDEX IF NOT EXISTS idx_member_monthly_stats_month_key ON member_monthly_stats (month_key)")
    await cursor.execute("CREATE INDEX IF NOT EXISTS idx_member_monthly_stats_member_id ON member_monthly_stats (member_id)")

    await conn.commit()
    await conn.close()

    # データベースの初期化が完了したことを通知
    print(f"データベース '{DB_FILE}' の初期化が完了しました。")


async def get_db_connection():
    conn = await aiosqlite.connect(DB_FILE)
    conn.row_factory = aiosqlite.Row # カラム名でアクセスできるようにする
    return conn

async def update_member_monthly_stats(month_key, member_id, duration):
    conn = await get_db_connection()
    cursor = await conn.cursor()
    await cursor.execute("""
        INSERT INTO member_monthly_stats (month_key, member_id, total_duration)
        VALUES (?, ?, ?)
        ON CONFLICT(month_key, member_id) DO UPDATE SET
            total_duration = total_duration + excluded.total_duration
    """, (month_key, str(member_id), duration))
    await conn.commit()
    await conn.close()

async def record_voice_session_to_db(session_start, session_duration, participants):
    conn = await get_db_connection()
    cursor = await conn.cursor()

    month_key = session_start.strftime("%Y-%m")
    start_time_iso = session_start.isoformat()

    # sessions テーブルにセッションを挿入
    await cursor.execute("""
        INSERT INTO sessions (month_key, start_time, duration)
        VALUES (?, ?, ?)
    """, (month_key, start_time_iso, session_duration))
    session_id = cursor.lastrowid # 挿入されたセッションのIDを取得

    # session_participants テーブルに参加者を挿入
    participant_data = [(session_id, str(p)) for p in participants]
    await cursor.executemany("""
        INSERT INTO session_participants (session_id, member_id)
        VALUES (?, ?)
    """, participant_data)

    await conn.commit()
    await conn.close()

async def get_total_call_time(member_id):
    conn = await get_db_connection()
    cursor = await conn.cursor()
    await cursor.execute("""
        SELECT SUM(total_duration) as total
        FROM member_monthly_stats
        WHERE member_id = ?
    """, (str(member_id),)) # member_idは文字列として保存されているため変換
    result = await cursor.fetchone()
    await conn.close()
    # 結果がNoneの場合（通話履歴がない場合）は0を返す
    return result['total'] if result and result['total'] is not None else 0

async def get_guild_settings(guild_id):
    conn = await get_db_connection()
    cursor = await conn.cursor()
    await cursor.execute("SELECT * FROM settings WHERE guild_id = ?", (str(guild_id),))
    settings = await cursor.fetchone()
    await conn.close()
    if settings:
        return settings
    else:
        # 設定がない場合はデフォルト値を返す (3時間 = 180分)
        return {"guild_id": str(guild_id), "lonely_timeout_minutes": 180, "reaction_wait_minutes": 5}

async def update_guild_settings(guild_id, lonely_timeout_minutes=None, reaction_wait_minutes=None):
    conn = await get_db_connection()
    cursor = await conn.cursor()
    settings = await get_guild_settings(guild_id)

    update_sql = "INSERT INTO settings (guild_id, lonely_timeout_minutes, reaction_wait_minutes) VALUES (?, ?, ?) ON CONFLICT(guild_id) DO UPDATE SET "
    params = [str(guild_id)]
    set_clauses = []

    if lonely_timeout_minutes is not None:
        set_clauses.append("lonely_timeout_minutes = ?")
        params.append(lonely_timeout_minutes)
    else:
        params.append(settings["lonely_timeout_minutes"])

    if reaction_wait_minutes is not None:
        set_clauses.append("reaction_wait_minutes = ?")
        params.append(reaction_wait_minutes)
    else:
        params.append(settings["reaction_wait_minutes"])

    update_sql += ", ".join(set_clauses)
    params.extend(params[1:])

    await cursor.execute(update_sql, params)
    await conn.commit()
    await conn.close()
