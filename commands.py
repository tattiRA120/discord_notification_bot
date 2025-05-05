import discord
from discord import app_commands
import datetime
from zoneinfo import ZoneInfo

import utils
from database import get_db_connection, get_total_call_time, get_guild_settings, update_guild_settings

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
        longest_date = utils.convert_utc_to_jst(datetime.datetime.fromisoformat(longest_session["start_time"])).strftime('%Y/%m/%d')
        longest_participants = longest_session.get("participants", [])
        longest_participants_names = []
        for mid in longest_participants:
            m_obj = guild.get_member(mid)
            if m_obj:
                longest_participants_names.append(m_obj.display_name)
            else:
                longest_participants_names.append(str(mid))
        longest_info = f"{utils.format_duration(longest_duration)}（{longest_date}）\n参加: {', '.join(longest_participants_names)}"
    else:
        longest_info = "なし"

    # メンバー別通話時間ランキング
    sorted_members = sorted(member_stats.items(), key=lambda x: x[1], reverse=True)
    ranking_lines = []
    for i, (member_id, duration) in enumerate(sorted_members, start=1):
        m_obj = guild.get_member(int(member_id))
        name = m_obj.display_name if m_obj else str(member_id)
        ranking_lines.append(f"{i}.  {utils.format_duration(duration)}  {name}")
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
    embed.add_field(name="平均通話時間", value=f"{utils.format_duration(monthly_avg)}", inline=False)
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
    longest_date = utils.convert_utc_to_jst(datetime.datetime.fromisoformat(longest_session["start_time"])).strftime('%Y/%m/%d')
    longest_participants = longest_session["participants"]
    longest_participants_names = []
    for mid in longest_participants:
        m_obj = guild.get_member(mid)
        if m_obj:
            longest_participants_names.append(m_obj.display_name)
        else:
            longest_participants_names.append(str(mid))
    longest_info = f"{utils.format_duration(longest_duration)}（{longest_date}）\n参加: {', '.join(longest_participants_names)}"

    # メンバー別ランキング（累計時間）
    sorted_members = sorted(members_total.items(), key=lambda x: x[1], reverse=True)
    ranking_lines = []
    for i, (member_id, duration) in enumerate(sorted_members, start=1):
        m_obj = guild.get_member(int(member_id))
        name = m_obj.display_name if m_obj else str(member_id)
        ranking_lines.append(f"{i}.  {utils.format_duration(duration)}  {name}")
    ranking_text = "\n".join(ranking_lines) if ranking_lines else "なし"

    embed = discord.Embed(title=f"【{year_display}】年間通話統計情報", color=0x00ff00)
    embed.add_field(name="年間: 平均通話時間", value=f"{utils.format_duration(avg_duration)}", inline=False)
    embed.add_field(name="年間: 最長通話", value=longest_info, inline=False)
    embed.add_field(name="年間: 通話時間ランキング", value=ranking_text, inline=False)
    return embed, year_display


# --- /monthly_stats コマンド ---
@app_commands.describe(month="表示する年月（形式: YYYY-MM）省略時は今月")
@app_commands.guild_only()
async def monthly_stats_callback(interaction: discord.Interaction, month: str = None):
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

monthly_stats = app_commands.Command(name="monthly_stats", description="月間の通話統計情報を表示します", callback=monthly_stats_callback)


# --- /total_time コマンド ---
@app_commands.describe(member="通話時間を確認するメンバー（省略時は自分）")
@app_commands.guild_only()
async def total_time_callback(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    total_seconds = await get_total_call_time(member.id)

    embed = discord.Embed(color=discord.Color.blue())
    embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)

    if total_seconds == 0:
        embed.add_field(name="総通話時間", value="通話履歴はありません。", inline=False)
    else:
        formatted_time = utils.format_duration(total_seconds)
        embed.add_field(name="総通話時間", value=formatted_time, inline=False)

    await interaction.response.send_message(embed=embed, ephemeral=True)

total_time = app_commands.Command(name="total_time", description="メンバーの累計通話時間を表示します", callback=total_time_callback)

# --- /total_call_ranking コマンド ---
@app_commands.guild_only()
async def call_ranking_callback(interaction: discord.Interaction):
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
                formatted_time = utils.format_duration(total_seconds)
                ranking_text += f"{i}. {formatted_time} {member.display_name}\n"
        embed.add_field(name="", value=ranking_text, inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

call_ranking = app_commands.Command(name="total_call_ranking", description="累計通話時間ランキングを表示します", callback=call_ranking_callback)

# --- /call_duration コマンド ---
@app_commands.guild_only()
async def call_duration_callback(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    now = datetime.datetime.now(datetime.timezone.utc)
    active_calls_found = False

    embed = discord.Embed(color=discord.Color.blue())
    embed.set_author(name="現在の通話状況")

    for key, session_data in utils.active_voice_sessions.items():
        if key[0] == guild_id:
            channel = utils.bot.get_channel(key[1])
            if channel and isinstance(channel, discord.VoiceChannel):
                duration_seconds = utils.calculate_call_duration_seconds(session_data["session_start"])
                formatted_duration = utils.format_duration(duration_seconds)
                embed.add_field(name=f"{channel.name}", value=formatted_duration, inline=False)
                active_calls_found = True

    if not active_calls_found:
        await interaction.response.send_message("現在、このサーバーで2人以上が参加している通話はありません。", ephemeral=True)
    else:
        await interaction.response.send_message(embed=embed, ephemeral=True)

call_duration = app_commands.Command(name="call_duration", description="現在の通話経過時間", callback=call_duration_callback)

# --- /help コマンド ---
@app_commands.guild_only()
async def help_callback(interaction: discord.Interaction):
    commands = utils.bot.tree.get_commands(guild=interaction.guild)
    embed = discord.Embed(title="コマンド一覧", color=0x00ff00)
    for command in commands:
        embed.add_field(name=command.name, value=command.description, inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)

help = app_commands.Command(name="help", description="利用可能なコマンド一覧を表示します", callback=help_callback)

# 管理者用：通知先チャンネル変更コマンド
@app_commands.default_permissions(administrator=True)
@app_commands.describe(channel="通知を送信するチャンネル")
@app_commands.guild_only()
async def changesendchannel_callback(interaction: discord.Interaction, channel: discord.TextChannel):
    guild_id = str(interaction.guild.id)
    if guild_id in utils.server_notification_channels and utils.server_notification_channels[guild_id] == channel.id:
        current_channel = utils.bot.get_channel(utils.server_notification_channels[guild_id])
        await interaction.response.send_message(f"すでに {current_channel.mention} で設定済みです。", ephemeral=True)
    else:
        utils.server_notification_channels[guild_id] = channel.id
        utils.save_channels_to_file()
        await interaction.response.send_message(f"通知先のチャンネルが {channel.mention} に設定されました。", ephemeral=True)

changesendchannel = app_commands.Command(name="changesendchannel", description="管理者用: 通知先のチャンネルを変更します", callback=changesendchannel_callback)

# 管理者用：年間統計情報送信デバッグコマンド
@app_commands.default_permissions(administrator=True)
@app_commands.describe(year="表示する年度（形式: YYYY）。省略時は今年")
@app_commands.guild_only()
async def debug_annual_stats_callback(interaction: discord.Interaction, year: str = None):
    # 年度の指定がなければ現在の年度を使用
    if year is None:
        now = datetime.datetime.now(ZoneInfo("Asia/Tokyo"))
        year = str(now.year)

    embed, display = await create_annual_stats_embed(interaction.guild, year)
    if embed:
        await interaction.response.send_message(embed=embed, ephemeral=True)
    else:
        await interaction.response.send_message(f"{display}の通話統計情報が記録されていません", ephemeral=True)

debug_annual_stats = app_commands.Command(name="debug_annual_stats", description="管理者用: 年間統計情報送信をデバッグします", callback=debug_annual_stats_callback)

# 管理者用：寝落ち確認設定変更コマンド
@app_commands.default_permissions(administrator=True)
@app_commands.describe(lonely_timeout_minutes="一人以下の状態が続く時間（分単位）", reaction_wait_minutes="反応を待つ時間（分単位）")
@app_commands.guild_only()
async def set_sleep_check_callback(interaction: discord.Interaction, lonely_timeout_minutes: int = None, reaction_wait_minutes: int = None):
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

set_sleep_check = app_commands.Command(name="set_sleep_check", description="管理者用: 寝落ち確認機能の設定を変更します", callback=set_sleep_check_callback)
