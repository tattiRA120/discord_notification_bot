import discord
from discord import app_commands
from discord.ext import commands, tasks
import datetime
import os
import json
import aiosqlite
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
import asyncio

# .envファイルの環境変数を読み込む
load_dotenv()

# データベースファイル名
DB_FILE = "voice_stats.db"

async def init_db():
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

# データベース初期化をボット起動時に行う
# 起動時に非同期関数を呼び出すため、asyncio.runを使用
asyncio.run(init_db())

# 環境変数からトークンを取得
TOKEN = os.getenv('DISCORD_BOT_TOKEN')

intents = discord.Intents.default()
intents.voice_states = True
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# サーバーごとの通知先チャンネルIDを保存する辞書
server_notification_channels = {}

# 通話開始時間と最初に通話を開始した人を記録する辞書（通話通知用）
call_sessions = {}

# 通知チャンネル設定を保存するファイルのパス
CHANNELS_FILE = "channels.json"

# 一人以下の状態になった通話チャンネルとその時刻、メンバー、関連タスクを記録する辞書
# キー: (guild_id, voice_channel_id), 値: {"start_time": datetimeオブジェクト, "member_id": int, "task": asyncio.Task}
lonely_voice_channels = {}

# 寝落ち確認メッセージとそれに対するリアクション監視タスクを記録する辞書
# キー: message_id, 値: {"member_id": int, "task": asyncio.Task}
sleep_check_messages = {}

# ボットがサーバーミュートしたメンバーのIDを記録するリスト
bot_muted_members = []


def save_channels_to_file():
    with open(CHANNELS_FILE, "w") as f:
        json.dump(server_notification_channels, f)

def load_channels_from_file():
    global server_notification_channels
    if os.path.exists(CHANNELS_FILE):
        with open(CHANNELS_FILE, "r") as f:
            try:
                content = f.read().strip()
                if content:
                    server_notification_channels = json.loads(content)
                else:
                    server_notification_channels = {}
            except json.JSONDecodeError:
                print(f"エラー: {CHANNELS_FILE} の読み込みに失敗しました。")
                server_notification_channels = {}
    else:
        server_notification_channels = {}
    # キーを文字列に統一
    server_notification_channels = {str(guild_id): channel_id for guild_id, channel_id in server_notification_channels.items()}

def convert_utc_to_jst(utc_time):
    return utc_time.astimezone(ZoneInfo("Asia/Tokyo"))

def format_duration(duration_seconds):
    seconds = int(duration_seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

# --- 二人以上の通話時間計算ヘルパー関数 ---
def calculate_call_duration_seconds(start_time):
    now = datetime.datetime.now(datetime.timezone.utc)
    return (now - start_time).total_seconds()

# (guild_id, channel_id) をキーに、現在進行中の「2人以上通話セッション」を記録する
active_voice_sessions = {}

# 2人以上が通話中のチャンネルを追跡するセット
active_status_channels = set()

# --- ステータス通話時間更新タスク ---
@tasks.loop(seconds=15)
async def update_call_status_task():
    if active_status_channels:
        # active_status_channelsからステータスに表示するチャンネルを選択
        channel_key_to_display = next(iter(active_status_channels))
        guild_id, channel_id = channel_key_to_display
        guild = bot.get_guild(guild_id)
        channel = bot.get_channel(channel_id)

        if guild and channel and channel_key_to_display in active_voice_sessions:
            # 選択したチャンネルの通話時間を計算し、ステータスに設定
            session_data = active_voice_sessions[channel_key_to_display]
            duration_seconds = calculate_call_duration_seconds(session_data["session_start"])
            formatted_duration = format_duration(duration_seconds)
            activity = discord.CustomActivity(name=f"{channel.name}: {formatted_duration}")
            await bot.change_presence(activity=activity)
        else:
            # チャンネルが見つからない、またはactive_voice_sessionsにない場合はセットから削除しステータスをクリア
            active_status_channels.discard(channel_key_to_display)
            if not active_status_channels:
                 await bot.change_presence(activity=None)
    else:
        # 2人以上の通話がない場合はステータスをクリア
        await bot.change_presence(activity=None)


# --- データベース操作関数 ---

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


# --- 10時間達成通知用ヘルパー関数 ---
async def check_and_notify_milestone(member: discord.Member, guild: discord.Guild, before_total: float, after_total: float):
    guild_id = str(guild.id)
    if guild_id not in server_notification_channels:
        return # 通知先チャンネルが設定されていない場合は何もしない

    notification_channel_id = server_notification_channels[guild_id]
    notification_channel = bot.get_channel(notification_channel_id)
    if not notification_channel:
        print(f"通知チャンネルが見つかりません: ギルドID {guild_id}, チャンネルID {notification_channel_id}")
        return

    hour_threshold = 10 * 3600 # 10時間 = 36000秒
    before_milestone = int(before_total // hour_threshold)
    after_milestone = int(after_total // hour_threshold)

    if after_milestone > before_milestone:
        achieved_hours = after_milestone * 10
        embed = discord.Embed(
            title="🎉 通話時間達成！ 🎉",
            description=f"{member.mention} さんの累計通話時間が **{achieved_hours}時間** を達成しました！",
            color=discord.Color.gold()
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="メンバー", value=member.display_name, inline=True)
        embed.add_field(name="達成時間", value=f"{achieved_hours} 時間", inline=True)
        embed.add_field(name="現在の総累計時間", value=format_duration(after_total), inline=False)
        embed.timestamp = datetime.datetime.now(ZoneInfo("Asia/Tokyo"))

        try:
            await notification_channel.send(embed=embed)
        except discord.Forbidden:
            print(f"エラー: チャンネル {notification_channel.name} ({notification_channel_id}) への送信権限がありません。")
        except Exception as e:
            print(f"通知送信中にエラーが発生しました: {e}")

# --- 月間統計作成用ヘルパー関数 ---
async def get_monthly_statistics(guild, month: str):
    conn = await get_db_connection()
    cursor = await conn.cursor()

    # 月間セッションの取得
    await cursor.execute("""
        SELECT start_time, duration, id FROM sessions
        WHERE month_key = ?
    """, (month,))
    sessions_data = await cursor.fetchall()

    sessions = []
    for session_row in sessions_data:
        # 参加者を取得
        await cursor.execute("""
            SELECT member_id FROM session_participants
            WHERE session_id = ?
        """, (session_row['id'],))
        participants_data = await cursor.fetchall()
        participants = [int(p['member_id']) for p in participants_data] # member_idをintに戻す

        sessions.append({
            "start_time": session_row['start_time'],
            "duration": session_row['duration'],
            "participants": participants
        })

    # メンバー別月間累計時間の取得
    await cursor.execute("""
        SELECT member_id, total_duration FROM member_monthly_stats
        WHERE month_key = ?
    """, (month,))
    member_stats_data = await cursor.fetchall()
    member_stats = {m['member_id']: m['total_duration'] for m in member_stats_data}

    await conn.close()

    # 平均通話時間の計算
    if sessions:
        monthly_avg = sum(sess["duration"] for sess in sessions) / len(sessions)
    else:
        monthly_avg = 0

    # 最長通話の情報
    if sessions:
        longest_session = max(sessions, key=lambda s: s["duration"])
        longest_duration = longest_session["duration"]
        # UTCのISO形式からJSTに変換
        longest_date = convert_utc_to_jst(datetime.datetime.fromisoformat(longest_session["start_time"])).strftime('%Y/%m/%d')
        longest_participants = longest_session.get("participants", [])
        longest_participants_names = []
        for mid in longest_participants:
            m_obj = guild.get_member(mid)
            if m_obj:
                longest_participants_names.append(m_obj.display_name)
            else:
                longest_participants_names.append(str(mid))
        longest_info = f"{format_duration(longest_duration)}（{longest_date}）\n参加: {', '.join(longest_participants_names)}"
    else:
        longest_info = "なし"

    # メンバー別通話時間ランキング
    sorted_members = sorted(member_stats.items(), key=lambda x: x[1], reverse=True)
    ranking_lines = []
    for i, (member_id, duration) in enumerate(sorted_members, start=1):
        m_obj = guild.get_member(int(member_id))
        name = m_obj.display_name if m_obj else str(member_id)
        ranking_lines.append(f"{i}.  {format_duration(duration)}  {name}")
    ranking_text = "\n".join(ranking_lines) if ranking_lines else "なし"

    return monthly_avg, longest_info, ranking_text

# --- 月間統計Embed作成用ヘルパー関数 ---
async def create_monthly_stats_embed(guild, month: str):
    try:
        year, mon = month.split("-")
        month_display = f"{year}年{mon}月"
    except Exception:
        month_display = month

    monthly_avg, longest_info, ranking_text = await get_monthly_statistics(guild, month)

    # 統計情報が取得できたかチェック
    if monthly_avg == 0 and longest_info == "なし" and ranking_text == "なし":
         return None, month_display

    embed = discord.Embed(title=f"【{month_display}】通話統計情報", color=0x00ff00)
    embed.add_field(name="平均通話時間", value=f"{format_duration(monthly_avg)}", inline=False)
    embed.add_field(name="最長通話", value=longest_info, inline=False)
    embed.add_field(name="通話時間ランキング", value=ranking_text, inline=False)

    return embed, month_display

# --- 年間統計作成用ヘルパー関数 ---
async def create_annual_stats_embed(guild, year: str):
    conn = await get_db_connection()
    cursor = await conn.cursor()

    # 対象年度のセッションを全て取得
    await cursor.execute("""
        SELECT start_time, duration, id FROM sessions
        WHERE strftime('%Y', start_time) = ?
    """, (year,))
    sessions_data = await cursor.fetchall()

    sessions_all = []
    for session_row in sessions_data:
         # 参加者を取得
        await cursor.execute("""
            SELECT member_id FROM session_participants
            WHERE session_id = ?
        """, (session_row['id'],))
        participants_data = await cursor.fetchall()
        participants = [int(p['member_id']) for p in participants_data] # member_idをintに戻す

        sessions_all.append({
            "start_time": session_row['start_time'],
            "duration": session_row['duration'],
            "participants": participants
        })

    # 対象年度のメンバー別累計時間を全て取得
    await cursor.execute("""
        SELECT member_id, SUM(total_duration) as total_duration
        FROM member_monthly_stats
        WHERE strftime('%Y', month_key) = ?
        GROUP BY member_id
    """, (year,))
    members_total_data = await cursor.fetchall()
    members_total = {m['member_id']: m['total_duration'] for m in members_total_data}

    await conn.close()

    year_display = f"{year}年"
    if not sessions_all:
        return None, year_display

    total_duration = sum(sess["duration"] for sess in sessions_all)
    total_sessions = len(sessions_all)
    avg_duration = total_duration / total_sessions if total_sessions else 0

    # 最長セッション
    longest_session = max(sessions_all, key=lambda s: s["duration"])
    longest_duration = longest_session["duration"]
    longest_date = convert_utc_to_jst(datetime.datetime.fromisoformat(longest_session["start_time"])).strftime('%Y/%m/%d')
    longest_participants = longest_session["participants"]
    longest_participants_names = []
    for mid in longest_participants:
        m_obj = guild.get_member(mid)
        if m_obj:
            longest_participants_names.append(m_obj.display_name)
        else:
            longest_participants_names.append(str(mid))
    longest_info = f"{format_duration(longest_duration)}（{longest_date}）\n参加: {', '.join(longest_participants_names)}"

    # メンバー別ランキング（累計時間）
    sorted_members = sorted(members_total.items(), key=lambda x: x[1], reverse=True)
    ranking_lines = []
    for i, (member_id, duration) in enumerate(sorted_members, start=1):
        m_obj = guild.get_member(int(member_id))
        name = m_obj.display_name if m_obj else str(member_id)
        ranking_lines.append(f"{i}.  {format_duration(duration)}  {name}")
    ranking_text = "\n".join(ranking_lines) if ranking_lines else "なし"

    embed = discord.Embed(title=f"【{year_display}】年間通話統計情報", color=0x00ff00)
    embed.add_field(name="年間: 平均通話時間", value=f"{format_duration(avg_duration)}", inline=False)
    embed.add_field(name="年間: 最長通話", value=longest_info, inline=False)
    embed.add_field(name="年間: 通話時間ランキング", value=ranking_text, inline=False)
    return embed, year_display

# --- 寝落ち確認とミュート処理 ---
async def check_lonely_channel(guild_id: int, channel_id: int, member_id: int):
    await asyncio.sleep(await get_lonely_timeout_seconds(guild_id)) # 設定された時間待機

    # 再度チャンネルの状態を確認
    guild = bot.get_guild(guild_id)
    if not guild:
        return
    channel = guild.get_channel(channel_id)
    if not channel or len(channel.members) > 1 or (len(channel.members) == 1 and channel.members[0].id != member_id):
        # チャンネルが存在しない、複数人になった、または別の人が一人になった場合は処理しない
        if (guild_id, channel_id) in lonely_voice_channels:
             lonely_voice_channels.pop((guild_id, channel_id)) # 状態管理から削除
        return

    # まだ一人以下の状態の場合、寝落ち確認メッセージを送信
    if str(guild_id) in server_notification_channels:
        notification_channel_id = server_notification_channels[str(guild_id)]
        notification_channel = bot.get_channel(notification_channel_id)
        if notification_channel:
            lonely_member = guild.get_member(member_id)
            if lonely_member:
                embed = discord.Embed(
                    title="寝落ちミュート",
                    description=f"{lonely_member.mention} さん、{channel.name} chで一人になってから時間が経ちました。\n寝落ちしていませんか？反応がない場合、自動でサーバーミュートします。\nミュートをキャンセルする場合は、 :white_check_mark: を押してください。",
                    color=discord.Color.orange()
                )
                try:
                    message = await notification_channel.send(embed=embed, ephemeral=True)
                    await message.add_reaction("✅") # :white_check_mark: 絵文字を追加

                    # リアクション監視タスクを開始
                    reaction_task = asyncio.create_task(wait_for_reaction(message.id, member_id, guild_id, channel_id))
                    sleep_check_messages[message.id] = {"member_id": member_id, "task": reaction_task}

                except discord.Forbidden:
                    print(f"エラー: チャンネル {notification_channel.name} ({notification_channel_id}) への送信権限がありません。")
                except Exception as e:
                    print(f"寝落ち確認メッセージ送信中にエラーが発生しました: {e}")
            else:
                 # メンバーが見つからない場合も状態管理から削除
                 if (guild_id, channel_id) in lonely_voice_channels:
                    lonely_voice_channels.pop((guild_id, channel_id))
        else:
            print(f"通知チャンネルが見つかりません: ギルドID {guild_id}")
            # 通知チャンネルがない場合も状態管理から削除
            if (guild_id, channel_id) in lonely_voice_channels:
                lonely_voice_channels.pop((guild_id, channel_id))
    else:
        print(f"ギルド {guild.name} ({guild_id}) の通知チャンネルが設定されていません。寝落ち確認メッセージを送信できません。")
        # 通知チャンネルが設定されていない場合も状態管理から削除
        if (guild_id, channel_id) in lonely_voice_channels:
            lonely_voice_channels.pop((guild_id, channel_id))


async def wait_for_reaction(message_id: int, member_id: int, guild_id: int, channel_id: int):
    settings = await get_guild_settings(guild_id)
    wait_seconds = settings["reaction_wait_minutes"] * 60

    try:
        # 指定された絵文字、ユーザーからのリアクションを待つ
        def check(reaction, user):
            return user.id == member_id and str(reaction.emoji) == '✅' and reaction.message.id == message_id

        await bot.wait_for('reaction_add', timeout=wait_seconds, check=check)
        print(f"メンバー {member_id} がメッセージ {message_id} に反応しました。ミュート処理をキャンセルします。")
        guild = bot.get_guild(guild_id)
        if guild and str(guild_id) in server_notification_channels:
            notification_channel = bot.get_channel(server_notification_channels[str(guild_id)])
            if notification_channel:
                try:
                    member = guild.get_member(member_id)
                    if member:
                        embed = discord.Embed(title="寝落ちミュート", description=f"{member.mention} さんが反応しました。\nサーバーミュートをキャンセルしました。", color=discord.Color.green())
                        await notification_channel.send(embed=embed, ephemeral=True)
                except discord.Forbidden:
                    print(f"エラー: チャンネル {notification_channel.name} ({notification_channel.id}) への送信権限がありません。")
                except Exception as e:
                    print(f"ミュートキャンセルメッセージ送信中にエラーが発生しました: {e}")


    except asyncio.TimeoutError:
        # タイムアウトした場合、ミュート処理を実行
        print(f"メッセージ {message_id} への反応がありませんでした。メンバー {member_id} をミュートします。")
        guild = bot.get_guild(guild_id)
        if guild:
            member = guild.get_member(member_id)
            if member:
                try:
                    await member.edit(mute=True, deafen=True)
                    print(f"メンバー {member.display_name} ({member_id}) をミュートしました。")
                    # ボットがミュートしたメンバーを記録
                    if member.id not in bot_muted_members:
                        bot_muted_members.append(member.id)

                    if str(guild_id) in server_notification_channels:
                        notification_channel = bot.get_channel(server_notification_channels[str(guild_id)])
                        if notification_channel:
                            try:
                                embed = discord.Embed(title="寝落ちミュート", description=f"{member.mention} さんからの反応がなかったため、サーバーミュートしました。\n再入室するとサーバーミュートが解除されます。", color=discord.Color.red())
                                await notification_channel.send(embed=embed)
                            except discord.Forbidden:
                                print(f"エラー: チャンネル {notification_channel.name} ({notification_channel.id}) への送信権限がありません。")
                            except Exception as e:
                                print(f"ミュート実行メッセージ送信中にエラーが発生しました: {e}")

                except discord.Forbidden:
                    print(f"エラー: メンバー {member.display_name} ({member_id}) をミュートする権限がありません。")
                except Exception as e:
                    print(f"メンバーミュート中にエラーが発生しました: {e}")
            else:
                print(f"メンバー {member_id} が見つかりませんでした。")
        else:
            print(f"ギルド {guild_id} が見つかりませんでした。")

    finally:
        # 処理が完了したら、一時的な記録から削除
        if message_id in sleep_check_messages:
            sleep_check_messages.pop(message_id)
        # チャンネルの状態管理からも削除（ミュートされたか反応があったかで一人以下の状態は終了とみなす）
        if (guild_id, channel_id) in lonely_voice_channels:
             lonely_voice_channels.pop((guild_id, channel_id))


async def get_lonely_timeout_seconds(guild_id):
    settings = await get_guild_settings(guild_id)
    return settings["lonely_timeout_minutes"] * 60 # 分を秒に変換

# --- イベントハンドラ ---
@bot.event
async def on_voice_state_update(member, before, after):
    guild_id = member.guild.id
    now = datetime.datetime.now(datetime.timezone.utc)

    # 対象チャンネル（入室または退室対象）
    channel_before = before.channel
    channel_after = after.channel

    # 一人以下になった場合の処理
    # チャンネルから退出して一人になった場合、または最初から一人で通話に参加した場合
    if (channel_before is not None and len(channel_before.members) == 1) or \
       (channel_before is None and channel_after is not None and len(channel_after.members) == 1):
        target_channel = channel_before if channel_before is not None else channel_after
        lonely_member = target_channel.members[0]
        key = (guild_id, target_channel.id)
        if key not in lonely_voice_channels: # 既に一人以下の状態として記録されていない場合のみ
            lonely_voice_channels[key] = {
                "start_time": now,
                "member_id": lonely_member.id,
                "task": asyncio.create_task(check_lonely_channel(guild_id, target_channel.id, lonely_member.id)) # タイマー開始
            }
            print(f"チャンネル {target_channel.name} ({target_channel.id}) が一人以下になりました。メンバー: {lonely_member.display_name}")

    # 一人以下の状態から複数人になった場合の処理、またはチャンネルから全員退出した場合
    if channel_after is not None and len(channel_after.members) > 1:
        key = (guild_id, channel_after.id)
        if key in lonely_voice_channels:
            # タイマーをキャンセル
            lonely_voice_channels[key]["task"].cancel()
            lonely_voice_channels.pop(key)
            print(f"チャンネル {channel_after.name} ({channel_after.id}) が複数人になりました。一人以下の状態を解除し、タイマーをキャンセルしました。")
    elif channel_before is not None and len(channel_before.members) == 0:
         key = (guild_id, channel_before.id)
         if key in lonely_voice_channels:
            # タイマーをキャンセル
            lonely_voice_channels[key]["task"].cancel()
            lonely_voice_channels.pop(key)
            print(f"チャンネル {channel_before.name} ({channel_before.id}) から全員退出しました。一人以下の状態を解除し、タイマーをキャンセルしました。")


    # 同一チャンネル内での状態変化の場合は何もしない
    if channel_before == channel_after:
        return

    # 通話通知機能
    if before.channel is None and after.channel is not None:
        voice_channel_id = after.channel.id
        if guild_id not in call_sessions:
            call_sessions[guild_id] = {}
        if voice_channel_id not in call_sessions[guild_id]:
            start_time = now
            call_sessions[guild_id][voice_channel_id] = {"start_time": start_time, "first_member": member.id}
            jst_time = convert_utc_to_jst(start_time)
            embed = discord.Embed(title="通話開始", color=0xE74C3C)
            embed.set_thumbnail(url=f"{member.avatar.url}?size=128")
            embed.add_field(name="チャンネル", value=f"{after.channel.name}")
            embed.add_field(name="始めた人", value=f"{member.display_name}")
            embed.add_field(name="開始時間", value=f"{jst_time.strftime('%Y/%m/%d %H:%M:%S')}")
            if str(guild_id) in server_notification_channels:
                notification_channel = bot.get_channel(server_notification_channels[str(guild_id)])
                if notification_channel:
                    await notification_channel.send(content="@everyone", embed=embed, allowed_mentions=discord.AllowedMentions(everyone=True))
                else:
                    print(f"通知チャンネルが見つかりません: ギルドID {guild_id}")

        # ボットによってミュートされたメンバーが再入室した場合、ミュートを解除
        if member.id in bot_muted_members:
            async def unmute_after_delay(m: discord.Member):
                await asyncio.sleep(1) # 1秒待機
                try:
                    await m.edit(mute=False, deafen=False)
                    if m.id in bot_muted_members:
                        bot_muted_members.remove(m.id)
                    print(f"メンバー {m.display_name} ({m.id}) が再入室したためミュートを解除しました。")

                    if str(m.guild.id) in server_notification_channels:
                        notification_channel = bot.get_channel(server_notification_channels[str(m.guild.id)])
                        if notification_channel:
                            try:
                                embed = discord.Embed(title="寝落ちミュート", description=f"{m.mention} さんが再入室したため、サーバーミュートを解除しました。", color=discord.Color.green())
                                await notification_channel.send(embed=embed, ephemeral=True)
                            except discord.Forbidden:
                                print(f"エラー: チャンネル {notification_channel.name} ({notification_channel.id}) への送信権限がありません。")
                            except Exception as e:
                                print(f"再入室時ミュート解除メッセージ送信中にエラーが発生しました: {e}")

                except discord.Forbidden:
                    print(f"エラー: メンバー {m.display_name} ({m.id}) のミュートを解除する権限がありません。")
                except Exception as e:
                    print(f"メンバーミュート解除中にエラーが発生しました: {e}")

            asyncio.create_task(unmute_after_delay(member))

    elif before.channel is not None and after.channel is None:
        voice_channel_id = before.channel.id
        if guild_id in call_sessions and voice_channel_id in call_sessions[guild_id]:
            voice_channel = before.channel
            if len(voice_channel.members) == 0:
                session = call_sessions[guild_id].pop(voice_channel_id)
                start_time = session["start_time"]
                call_duration = (now - start_time).total_seconds()
                duration_str = format_duration(call_duration)
                embed = discord.Embed(title="通話終了", color=0x5865F2)
                embed.add_field(name="チャンネル", value=f"{voice_channel.name}")
                embed.add_field(name="通話時間", value=f"{duration_str}")
                if str(guild_id) in server_notification_channels:
                    notification_channel = bot.get_channel(server_notification_channels[str(guild_id)])
                    if notification_channel:
                        await notification_channel.send(embed=embed)
                    else:
                        print(f"通知チャンネルが見つかりません: ギルドID {guild_id}")

    # --- 2人以上通話状態の記録（各メンバーごとに個別記録＋全参加者リストを維持する処理） ---

    # 退室処理（before.channel から退出した場合）
    if channel_before is not None:
        key = (guild_id, channel_before.id)
        if key in active_voice_sessions:
            session_data = active_voice_sessions[key]
            # もし対象メンバーが在室中ならその個人分の退室処理を実施
            if member.id in session_data["current_members"]:
                join_time = session_data["current_members"].pop(member.id)
                duration = (now - join_time).total_seconds()

                # --- 10時間達成チェック ---
                before_total = await get_total_call_time(member.id)
                month_key = join_time.strftime("%Y-%m")
                await update_member_monthly_stats(month_key, member.id, duration)
                after_total = await get_total_call_time(member.id)
                await check_and_notify_milestone(member, member.guild, before_total, after_total)
                # --- ここまで ---

            # もし退室後、チャンネル内人数が1人以下ならセッション終了処理を実施
            if channel_before is not None and len(channel_before.members) < 2:
                # セッション終了時の残メンバーの統計更新と通知チェック
                remaining_members_data = session_data["current_members"].copy()
                for m_id, join_time in remaining_members_data.items():
                    d = (now - join_time).total_seconds()

                    # --- 10時間達成チェック (セッション終了時) ---
                    m_obj = member.guild.get_member(m_id)
                    if m_obj:
                        before_total_sess_end = await get_total_call_time(m_id)
                        month_key = join_time.strftime("%Y-%m")
                        await update_member_monthly_stats(month_key, m_id, d)
                        after_total_sess_end = await get_total_call_time(m_id)
                        await check_and_notify_milestone(m_obj, member.guild, before_total_sess_end, after_total_sess_end)
                    else:
                         month_key = join_time.strftime("%Y-%m")
                         await update_member_monthly_stats(month_key, m_id, d)
                    # --- ここまで ---

                    session_data["current_members"].pop(m_id)

                overall_duration = (now - session_data["session_start"]).total_seconds()
                await record_voice_session_to_db(session_data["session_start"], overall_duration, list(session_data["all_participants"]))
                active_voice_sessions.pop(key, None)

                # チャンネルの人数が1人以下になったら active_status_channels から削除
                if channel_before is not None and len(channel_before.members) < 2:
                    active_status_channels.discard(key)
                    # 2人以上の通話がすべて終了した場合、ステータス更新タスクを停止しステータスをクリア
                    if not active_status_channels and update_call_status_task.is_running():
                        update_call_status_task.stop()
                        await bot.change_presence(activity=None)


    # 入室処理（after.channelに入室した場合）
    if channel_after is not None:
        key = (guild_id, channel_after.id)
        # チャンネル内の人数が2人以上の場合
        if len(channel_after.members) >= 2:
            if key not in active_voice_sessions:
                # セッション開始時刻は、通話が2人以上になった時刻（この時点の now）
                active_voice_sessions[key] = {
                    "session_start": now,
                    "current_members": { m.id: now for m in channel_after.members },
                    "all_participants": set(m.id for m in channel_after.members)
                }
            else:
                # 既存のセッションがある場合、新たに入室したメンバーを更新する
                session_data = active_voice_sessions[key]
                for m in channel_after.members:
                    if m.id not in session_data["current_members"]:
                        session_data["current_members"][m.id] = now
                    session_data["all_participants"].add(m.id)

            # チャンネルの人数が2人以上になったら active_status_channels に追加
            if key not in active_status_channels:
                active_status_channels.add(key)
                # 初めて2人以上の通話が始まった場合、ステータス更新タスクを開始
                if not update_call_status_task.is_running():
                    update_call_status_task.start()

        else:
            # 人数が2人未満の場合は、既にセッションが存在する場合のみ更新する
            if key in active_voice_sessions:
                session_data = active_voice_sessions[key]
                for m in channel_after.members:
                    if m.id not in session_data["current_members"]:
                        session_data["current_members"][m.id] = now
                    session_data["all_participants"].add(m.id)

# --- /monthly_stats コマンド ---
@bot.tree.command(name="monthly_stats", description="月間の通話統計情報を表示します")
@app_commands.describe(month="表示する年月（形式: YYYY-MM）省略時は今月")
@app_commands.guild_only()
async def monthly_stats(interaction: discord.Interaction, month: str = None):
    if month is None:
        now = datetime.datetime.now(datetime.timezone.utc)
        month = now.strftime("%Y-%m")

    try:
        year, mon = month.split("-")
        month_display = f"{year}年{mon}月"
    except ValueError:
        await interaction.response.send_message("指定された月の形式が正しくありません。形式は YYYY-MM で指定してください。", ephemeral=True)
        return

    embed, month_display = await create_monthly_stats_embed(interaction.guild, month)

    if embed is None:
        await interaction.response.send_message(f"{month_display}は通話統計情報が記録されていません", ephemeral=True)
        return

    await interaction.response.send_message(embed=embed, ephemeral=True)

# --- /total_time コマンド ---
@bot.tree.command(name="total_time", description="メンバーの累計通話時間を表示します")
@app_commands.describe(member="通話時間を確認するメンバー（省略時は自分）")
@app_commands.guild_only()
async def total_time(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    total_seconds = await get_total_call_time(member.id)

    embed = discord.Embed(color=discord.Color.blue())
    embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)

    if total_seconds == 0:
        embed.add_field(name="総通話時間", value="通話履歴はありません。", inline=False)
    else:
        formatted_time = format_duration(total_seconds)
        embed.add_field(name="総通話時間", value=formatted_time, inline=False)

    await interaction.response.send_message(embed=embed, ephemeral=True)

# --- /total_call_ranking コマンド ---
@bot.tree.command(name="total_call_ranking", description="累計通話時間ランキングを表示します")
@app_commands.guild_only()
async def call_ranking(interaction: discord.Interaction):
    guild = interaction.guild
    members = guild.members

    # メンバーの通話時間を取得
    member_call_times = {}
    for member in members:
        total_seconds = await get_total_call_time(member.id)
        if total_seconds > 0:  # 通話時間が0より大きいメンバーのみを追加
            member_call_times[member.id] = total_seconds

    # 通話時間でランキングを作成
    sorted_members = sorted(member_call_times.items(), key=lambda x: x[1], reverse=True)

    if not sorted_members:
        await interaction.response.send_message("通話履歴がないため、ランキングを表示できません。", ephemeral=True)
    else:
        embed = discord.Embed(title="総通話時間ランキング", color=discord.Color.gold())
        ranking_text = ""
        for i, (member_id, total_seconds) in enumerate(sorted_members[:10], start=1):  # 上位10名を表示
            member = guild.get_member(member_id)
            if member:
                formatted_time = format_duration(total_seconds)
                ranking_text += f"{i}. {formatted_time} {member.display_name}\n"
        embed.add_field(name="", value=ranking_text, inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

# --- /call_duration コマンド ---
@bot.tree.command(name="call_duration", description="現在の通話経過時間")
@app_commands.guild_only()
async def call_duration(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    now = datetime.datetime.now(datetime.timezone.utc)
    active_calls_found = False

    embed = discord.Embed(color=discord.Color.blue())
    embed.set_author(name="現在の通話状況")

    for key, session_data in active_voice_sessions.items():
        if key[0] == guild_id:
            channel = bot.get_channel(key[1])
            if channel and isinstance(channel, discord.VoiceChannel):
                duration_seconds = calculate_call_duration_seconds(session_data["session_start"])
                formatted_duration = format_duration(duration_seconds)
                embed.add_field(name=f"{channel.name}", value=formatted_duration, inline=False)
                active_calls_found = True

    if not active_calls_found:
        await interaction.response.send_message("現在、このサーバーで2人以上が参加している通話はありません。", ephemeral=True)
    else:
        await interaction.response.send_message(embed=embed, ephemeral=True)

# --- /help コマンド ---
@bot.tree.command(name="help", description="利用可能なコマンド一覧を表示します")
@app_commands.guild_only()
async def help(interaction: discord.Interaction):
    commands = bot.tree.get_commands(guild=interaction.guild)
    embed = discord.Embed(title="コマンド一覧", color=0x00ff00)
    for command in commands:
        embed.add_field(name=command.name, value=command.description, inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)

# 管理者用：通知先チャンネル変更コマンド
@bot.tree.command(name="changesendchannel", description="管理者用: 通知先のチャンネルを変更します")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(channel="通知を送信するチャンネル")
@app_commands.guild_only()
async def changesendchannel(interaction: discord.Interaction, channel: discord.TextChannel):
    guild_id = str(interaction.guild.id)
    if guild_id in server_notification_channels and server_notification_channels[guild_id] == channel.id:
        current_channel = bot.get_channel(server_notification_channels[guild_id])
        await interaction.response.send_message(f"すでに {current_channel.mention} で設定済みです。", ephemeral=True)
    else:
        server_notification_channels[guild_id] = channel.id
        save_channels_to_file()
        await interaction.response.send_message(f"通知先のチャンネルが {channel.mention} に設定されました。", ephemeral=True)

# 管理者用：年間統計情報送信デバッグコマンド
@bot.tree.command(name="debug_annual_stats", description="管理者用: 年間統計情報送信をデバッグします")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(year="表示する年度（形式: YYYY）。省略時は今年")
@app_commands.guild_only()
async def debug_annual_stats(interaction: discord.Interaction, year: str = None):
    # 年度の指定がなければ現在の年度を使用
    if year is None:
        now = datetime.datetime.now(ZoneInfo("Asia/Tokyo"))
        year = str(now.year)

    embed, display = await create_annual_stats_embed(interaction.guild, year)
    if embed:
        await interaction.response.send_message(embed=embed, ephemeral=True)
    else:
        await interaction.response.send_message(f"{display}の通話統計情報が記録されていません", ephemeral=True)

# 管理者用：寝落ち確認設定変更コマンド
@bot.tree.command(name="set_sleep_check", description="管理者用: 寝落ち確認機能の設定を変更します")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(lonely_timeout_minutes="一人以下の状態が続く時間（分単位）", reaction_wait_minutes="反応を待つ時間（分単位）")
@app_commands.guild_only()
async def set_sleep_check(interaction: discord.Interaction, lonely_timeout_minutes: int = None, reaction_wait_minutes: int = None):
    if lonely_timeout_minutes is None and reaction_wait_minutes is None:
        settings = await get_guild_settings(interaction.guild.id)
        await interaction.response.send_message(
            f"現在の寝落ち確認設定:\n"
            f"一人以下の状態が続く時間: {settings['lonely_timeout_minutes']} 分\n"
            f"反応を待つ時間: {settings['reaction_wait_minutes']} 分",
            ephemeral=True
        )
        return

    if lonely_timeout_minutes is not None and lonely_timeout_minutes <= 0:
        await interaction.response.send_message("一人以下の状態が続く時間は1分以上に設定してください。", ephemeral=True)
        return
    if reaction_wait_minutes is not None and reaction_wait_minutes <= 0:
        await interaction.response.send_message("反応を待つ時間は1分以上に設定してください。", ephemeral=True)
        return

    await update_guild_settings(interaction.guild.id, lonely_timeout_minutes=lonely_timeout_minutes, reaction_wait_minutes=reaction_wait_minutes)
    settings = await get_guild_settings(interaction.guild.id)
    await interaction.response.send_message(
        f"寝落ち確認設定を更新しました:\n"
        f"一人以下の状態が続く時間: {settings['lonely_timeout_minutes']} 分\n"
        f"反応を待つ時間: {settings['reaction_wait_minutes']} 分",
        ephemeral=True
    )

# --- 毎日18時のトリガータスク ---
@tasks.loop(time=datetime.time(hour=18, minute=0, tzinfo=ZoneInfo("Asia/Tokyo")))
async def scheduled_stats():
    now = datetime.datetime.now(ZoneInfo("Asia/Tokyo"))

    # 前月の統計情報送信（毎月1日）
    if now.day == 1:
        first_day_current = now.replace(day=1)
        prev_month_last_day = first_day_current - datetime.timedelta(days=1)
        previous_month = prev_month_last_day.strftime("%Y-%m")

        for guild_id, channel_id in server_notification_channels.items():
            guild = bot.get_guild(int(guild_id))
            channel = bot.get_channel(channel_id)
            if guild and channel:
                embed, month_display = await create_monthly_stats_embed(guild, previous_month)
                if embed:
                    await channel.send(embed=embed)
                else:
                    embed = discord.Embed(
                        title=f"【前月の通話統計】",
                        description=f"{month_display}は通話記録がありませんでした",
                        color=discord.Color.orange()
                    )
                    await channel.send(embed=embed)

    # 年間統計情報送信（毎年12月31日）
    if now.month == 12 and now.day == 31:
        year_str = str(now.year)
        for guild_id, channel_id in server_notification_channels.items():
            guild = bot.get_guild(int(guild_id))
            channel = bot.get_channel(channel_id)
            if guild and channel:
                embed, year_display = await create_annual_stats_embed(guild, year_str)
                if embed:
                    await channel.send(embed=embed)
                else:
                    embed = discord.Embed(
                        title=f"【年間の通話統計】",
                        description=f"{month_display}は通話記録がありませんでした",
                        color=discord.Color.orange()
                    )
                    await channel.send(embed=embed)

# --- 起動時処理 ---
@bot.event
async def on_ready():
    load_channels_from_file()

    print(f'ログインしました: {bot.user.name}')

    # 既存のグローバルコマンドを取得（内部属性 _global_commands を利用）
    global_cmds = list(bot.tree._global_commands.values())

    # グローバルコマンドを削除
    bot.tree.clear_commands(guild=None)
    await bot.tree.sync()

    # 各ギルドに対して、グローバルコマンドをコピーして再登録し、同期する
    for guild in bot.guilds:
        for cmd in global_cmds:
            bot.tree.add_command(cmd, guild=guild)
        print(f'接続中のサーバー: {guild.name} (ID: {guild.id})')
    await bot.tree.sync(guild=guild)

    scheduled_stats.start()

bot.run(TOKEN)
