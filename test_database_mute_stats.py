import pytest
import pytest_asyncio # Explicitly import for the decorator
import aiosqlite
import os
import sys

# Add the parent directory to the Python path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Import the module whose components will be patched or used
import database
import constants as app_constants

# Import the functions to be tested
from database import increment_mute_count, get_mute_count

# Test DB URI - using a unique name for the in-memory DB
TEST_DB_URI = "file:test_db_mute_stats_v8?mode=memory&cache=shared" # Incremented version for clarity

@pytest_asyncio.fixture(scope="function")
async def actual_fixed_db_conn():
    """
    Provides a connection to an in-memory SQLite database with the schema pre-applied
    and row_factory set to aiosqlite.Row.
    """
    conn = await aiosqlite.connect(TEST_DB_URI)
    conn.row_factory = aiosqlite.Row # <--- FIX: Set row_factory here
    cursor = await conn.cursor()

    # Manually execute all CREATE TABLE and CREATE INDEX statements
    await cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS {app_constants.TABLE_SESSIONS} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            {app_constants.COLUMN_MONTH_KEY} TEXT NOT NULL,
            {app_constants.COLUMN_START_TIME} TEXT NOT NULL,
            duration INTEGER NOT NULL
        )
    """)
    await cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS {app_constants.TABLE_SESSION_PARTICIPANTS} (
            {app_constants.COLUMN_SESSION_ID} INTEGER,
            {app_constants.COLUMN_MEMBER_ID} INTEGER NOT NULL,
            PRIMARY KEY ({app_constants.COLUMN_SESSION_ID}, {app_constants.COLUMN_MEMBER_ID}),
            FOREIGN KEY ({app_constants.COLUMN_SESSION_ID}) REFERENCES {app_constants.TABLE_SESSIONS}(id) ON DELETE CASCADE
        )
    """)
    await cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS {app_constants.TABLE_MEMBER_MONTHLY_STATS} (
            {app_constants.COLUMN_MONTH_KEY} TEXT NOT NULL,
            {app_constants.COLUMN_MEMBER_ID} INTEGER NOT NULL,
            {app_constants.COLUMN_TOTAL_DURATION} INTEGER NOT NULL DEFAULT {app_constants.DEFAULT_TOTAL_DURATION},
            PRIMARY KEY ({app_constants.COLUMN_MONTH_KEY}, {app_constants.COLUMN_MEMBER_ID})
        )
    """)
    await cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS {app_constants.TABLE_SETTINGS} (
            {app_constants.COLUMN_GUILD_ID} TEXT PRIMARY KEY,
            {app_constants.COLUMN_LONELY_TIMEOUT_MINUTES} INTEGER DEFAULT {app_constants.DEFAULT_LONELY_TIMEOUT_MINUTES},
            {app_constants.COLUMN_REACTION_WAIT_MINUTES} INTEGER DEFAULT {app_constants.DEFAULT_REACTION_WAIT_MINUTES}
        )
    """)
    await cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS {app_constants.TABLE_USER_MUTE_STATS} (
            {app_constants.COLUMN_MEMBER_ID} INTEGER PRIMARY KEY,
            {app_constants.COLUMN_MUTE_COUNT} INTEGER NOT NULL DEFAULT 0
        )
    """)
    # Indexes
    await cursor.execute(f"CREATE INDEX IF NOT EXISTS idx_sessions_month_key ON {app_constants.TABLE_SESSIONS} ({app_constants.COLUMN_MONTH_KEY})")
    await cursor.execute(f"CREATE INDEX IF NOT EXISTS idx_session_participants_session_id ON {app_constants.TABLE_SESSION_PARTICIPANTS} ({app_constants.COLUMN_SESSION_ID})")
    await cursor.execute(f"CREATE INDEX IF NOT EXISTS idx_session_participants_member_id ON {app_constants.TABLE_SESSION_PARTICIPANTS} ({app_constants.COLUMN_MEMBER_ID})")
    await cursor.execute(f"CREATE INDEX IF NOT EXISTS idx_member_monthly_stats_month_key ON {app_constants.TABLE_MEMBER_MONTHLY_STATS} ({app_constants.COLUMN_MONTH_KEY})")
    await cursor.execute(f"CREATE INDEX IF NOT EXISTS idx_member_monthly_stats_member_id ON {app_constants.TABLE_MEMBER_MONTHLY_STATS} ({app_constants.COLUMN_MEMBER_ID})")

    await conn.commit()
    yield conn
    await conn.close()

@pytest.fixture(autouse=True)
def patch_database_connection_class(monkeypatch, actual_fixed_db_conn):
    """
    Autouse synchronous fixture that replaces the entire DatabaseConnection class
    with a mock that uses the actual_fixed_db_conn.
    """
    class MockDatabaseConnection:
        def __init__(self):
            pass

        async def __aenter__(self):
            return actual_fixed_db_conn

        async def __aexit__(self, exc_type, exc, tb):
            pass

    monkeypatch.setattr(database, 'DatabaseConnection', MockDatabaseConnection)
    monkeypatch.setattr(database, 'DB_FILE', TEST_DB_URI)
    monkeypatch.setattr(app_constants, 'DB_FILE_NAME', TEST_DB_URI)


@pytest.mark.asyncio
async def test_increment_mute_count_new_user():
    user_id = 12345
    await increment_mute_count(user_id)
    count = await get_mute_count(user_id)
    assert count == 1, "Mute count for a new user should be 1 after first increment."

@pytest.mark.asyncio
async def test_increment_mute_count_existing_user():
    user_id = 54321
    await increment_mute_count(user_id)
    await increment_mute_count(user_id)
    count = await get_mute_count(user_id)
    assert count == 2, "Mute count should be 2 after second increment."

    await increment_mute_count(user_id)
    count_after_third_increment = await get_mute_count(user_id)
    assert count_after_third_increment == 3, "Mute count should be 3 after third increment."

@pytest.mark.asyncio
async def test_get_mute_count_existing_user():
    user_id = 98765
    await increment_mute_count(user_id)
    await increment_mute_count(user_id)
    await increment_mute_count(user_id)
    count = await get_mute_count(user_id)
    assert count == 3, "Should retrieve the correct mute count for an existing user."

@pytest.mark.asyncio
async def test_get_mute_count_non_existent_user():
    user_id = 112233
    count = await get_mute_count(user_id)
    assert count == 0, "Mute count for a non-existent user should be 0."

@pytest.mark.asyncio
async def test_multiple_users_mute_counts():
    user_id_a = 101010
    user_id_b = 202020

    await increment_mute_count(user_id_a)
    await increment_mute_count(user_id_a)
    await increment_mute_count(user_id_b)

    count_a = await get_mute_count(user_id_a)
    count_b = await get_mute_count(user_id_b)

    assert count_a == 2, "User A's mute count is incorrect."
    assert count_b == 1, "User B's mute count is incorrect."

    user_id_c = 303030
    count_c = await get_mute_count(user_id_c)
    assert count_c == 0, "User C's mute count should be 0."
