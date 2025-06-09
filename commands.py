import discord
from discord import app_commands
from discord.ext import commands # Cog を使用するためにインポート
import datetime
from zoneinfo import ZoneInfo
import logging

from database import (
    get_db_connection, get_total_call_time, get_guild_settings, update_guild_settings,
    get_monthly_voice_sessions, get_monthly_member_stats, get_annual_voice_sessions, get_annual_member_total_stats,
    get_participants_by_session_ids, get_total_call_time_for_guild_members, get_mute_count
)
import config
import voice_state_manager
import formatters
from voice_events import SleepCheckManager
import constants

# ロガーを取得
logger = logging.getLogger(__name__)

# --- コマンドを格納する Cog クラス ---
class BotCommands(commands.Cog):
    def __init__(self, bot, sleep_check_manager, voice_state_manager):
        self.bot = bot
        self.sleep_check_manager = sleep_check_manager
        self.voice_state_manager = voice_state_manager
        logger.info("BotCommands Cog initialized.")

    # --- メンバーIDリストから表示名リストを取得するヘルパー関数 ---
    def _get_member_display_names(self, guild, member_ids):
        """
        メンバーIDのリストを受け取り、対応するメンバーの表示名のリストを返します。
        メンバーが見つからない場合はIDを文字列として返します。
        """
        member_lookup = {member.id: member for member in guild.members}
        display_names = []
        for mid in member_ids:
            m_obj = member_lookup.get(mid)
            if m_obj:
                display_names.append(m_obj.display_name)
            else:
                display_names.append(str(mid)) # メンバーが見つからない場合はIDを表示
        return display_names

    # --- 月間統計作成用ヘルパー関数 ---
    # 指定された月の通話統計情報をデータベースから取得し、整形して返します。
    # データベース操作、データ集計、最長通話やランキングの算出を含みます。
    async def _get_monthly_statistics(self, guild, month: str):
        logger.info(f"Fetching monthly statistics for guild {guild.id}, month {month}")

        # database.py から指定された月の全セッションを取得
        # データベースエラーはdatabase.py内で処理され、空のリストが返されます。
        sessions_data = await get_monthly_voice_sessions(month)
        logger.debug(f"Found {len(sessions_data)} sessions for month {month}")

        # database.py から指定された月のメンバー別累計通話時間を取得
        # データベースエラーはdatabase.py内で処理され、空の辞書が返されます。
        member_stats = await get_monthly_member_stats(month)
        logger.debug(f"Found stats for {len(member_stats)} members for month {month}")

        # セッションデータがない場合は平均通話時間などを0に設定
        if not sessions_data:
            monthly_avg = 0
            longest_info = "なし"
            logger.debug("No sessions found, monthly average is 0.")
        else:
            # 月間平均通話時間の計算
            monthly_avg = sum(sess["duration"] for sess in sessions_data) / len(sessions_data)
            logger.debug(f"Calculated monthly average: {monthly_avg}")

            # 最長通話の情報取得
            longest_session = max(sessions_data, key=lambda s: s["duration"])
            longest_duration = longest_session["duration"]
            # UTCのISO形式からJSTに変換して日付をフォーマット
            longest_date = formatters.convert_utc_to_jst(datetime.datetime.fromisoformat(longest_session["start_time"])).strftime('%Y/%m/%d')

            # 最長セッションの参加者を取得
            longest_session_id = longest_session["id"] # セッションIDを取得
            # データベースエラーはdatabase.py内で処理され、空の辞書が返されます。
            participants_map = await get_participants_by_session_ids([longest_session_id])
            longest_participants = participants_map.get(longest_session_id, []) # 参加者リストを取得

            longest_participants_names = self._get_member_display_names(guild, longest_participants)
            longest_info = f"{formatters.format_duration(longest_duration)}（{longest_date}）\n参加: {', '.join(longest_participants_names)}"
            logger.debug(f"Longest session: {longest_info}")


        # メンバー別通話時間ランキングの作成
        sorted_members = sorted(member_stats.items(), key=lambda x: x[1], reverse=True)
        ranking_lines = []
        member_ids_in_ranking = [member_id for member_id, duration in sorted_members]
        ranking_display_names = self._get_member_display_names(guild, member_ids_in_ranking)

        for i, (member_id, duration) in enumerate(sorted_members, start=1):
            name = ranking_display_names[i-1] # ヘルパー関数で取得した表示名を使用
            ranking_lines.append(f"{i}.  {formatters.format_duration(duration)}  {name}")
        ranking_text = "\n".join(ranking_lines) if ranking_lines else "なし"
        logger.debug(f"Ranking text generated:\n{ranking_text}")

        # 平均通話時間、最長通話情報、ランキングテキストを返す
        return monthly_avg, longest_info, ranking_text

    # --- 月間統計Embed作成用ヘルパー関数 ---
    # _get_monthly_statistics から取得した情報をもとに、月間統計表示用のEmbedを作成します。
    async def _create_monthly_stats_embed(self, guild, month: str):
        logger.info(f"Creating monthly stats embed for guild {guild.id}, month {month}")
        try:
            year, mon = month.split("-")
            month_display = f"{year}年{mon}月"
        except Exception:
            month_display = month # フォーマットが不正な場合はそのまま表示
            logger.warning(f"Invalid month format: {month}")

        # 月間統計情報を取得
        monthly_avg, longest_info, ranking_text = await self._get_monthly_statistics(guild, month)

        # 統計情報が取得できたかチェックし、データがない場合はNoneを返す
        if monthly_avg == 0 and longest_info == "なし" and ranking_text == "なし":
            logger.info(f"No statistics recorded for {month_display}")
            return None, month_display

        # Embedを作成し、フィールドを追加
        embed = discord.Embed(title=f"【{month_display}】通話統計情報", color=constants.EMBED_COLOR_SUCCESS)
        embed.add_field(name=constants.EMBED_FIELD_AVERAGE_CALL_TIME, value=f"{formatters.format_duration(monthly_avg)}", inline=False)
        embed.add_field(name=constants.EMBED_FIELD_LONGEST_CALL, value=longest_info, inline=False)
        embed.add_field(name=constants.EMBED_FIELD_CALL_RANKING, value=ranking_text, inline=False)
        logger.debug("Monthly stats embed created successfully.")

        # 作成したEmbedと表示用の月を返す
        return embed, month_display

    # --- 年間統計データ取得・処理・作成用ヘルパー関数 ---
    # 指定された年度の通話統計情報をデータベースから取得し、整形して返します。
    # データベース操作、データ集計、最長通話やランキングの算出を含みます。
    async def get_and_process_annual_stats_data(self, guild, year: str):
        logger.info(f"Fetching and processing annual statistics for guild {guild.id}, year {year}")

        # database.py から指定された年度の全セッションを取得
        # データベースエラーはdatabase.py内で処理され、空のリストが返されます。
        sessions_data = await get_annual_voice_sessions(year)
        logger.debug(f"Found {len(sessions_data)} sessions for year {year}")

        # database.py から指定された年度のメンバー別累計通話時間を取得
        # データベースエラーはdatabase.py内で処理され、空の辞書が返されます。
        members_total = await get_annual_member_total_stats(year)
        logger.debug(f"Found stats for {len(members_total)} members for year {year}")

        year_display = f"{year}年"
        # セッションデータがない場合はNoneを返す
        if not sessions_data:
            logger.info(f"No sessions found for year {year}")
            return None, year_display, None, None, None, None

        # 年間合計通話時間、セッション数、平均通話時間の計算
        total_duration = sum(sess["duration"] for sess in sessions_data)
        total_sessions = len(sessions_data)
        avg_duration = total_duration / total_sessions if total_sessions else 0
        logger.debug(f"Calculated annual total duration: {total_duration}, total sessions: {total_sessions}, average duration: {avg_duration}")

        # 最長セッションの情報取得
        longest_session = max(sessions_data, key=lambda s: s["duration"])
        longest_duration = longest_session["duration"]
        # UTCのISO形式からJSTに変換して日付をフォーマット
        longest_date = formatters.convert_utc_to_jst(datetime.datetime.fromisoformat(longest_session["start_time"])).strftime('%Y/%m/%d')

        # 最長セッションの参加者を取得
        longest_session_id = longest_session["id"] # セッションIDを取得
        # データベースエラーはdatabase.py内で処理され、空の辞書が返されます。
        participants_map = await get_participants_by_session_ids([longest_session_id])
        longest_participants = participants_map.get(longest_session_id, []) # 参加者リストを取得

        longest_participants_names = self._get_member_display_names(guild, longest_participants)
        longest_info = f"{formatters.format_duration(longest_duration)}（{longest_date}）\n参加: {', '.join(longest_participants_names)}"
        logger.debug(f"Longest annual session: {longest_info}")

        # メンバー別ランキング（累計時間）の作成
        sorted_members = sorted(members_total.items(), key=lambda x: x[1], reverse=True)
        ranking_lines = []
        member_ids_in_ranking = [member_id for member_id, duration in sorted_members]
        ranking_display_names = self._get_member_display_names(guild, member_ids_in_ranking)

        for i, (member_id, duration) in enumerate(sorted_members, start=1):
            name = ranking_display_names[i-1] # ヘルパー関数で取得した表示名を使用
            ranking_lines.append(f"{i}.  {formatters.format_duration(duration)}  {name}")
        ranking_text = "\n".join(ranking_lines) if ranking_lines else "なし"
        logger.debug(f"Annual ranking text generated:\n{ranking_text}")

        # 処理したデータを返す
        return year_display, avg_duration, longest_info, ranking_text, sessions_data, members_total

    # --- 年間統計Embed作成用ヘルパー関数 ---
    # get_and_process_annual_stats_data から取得した情報をもとに、年間統計表示用のEmbedを作成します。
    async def _create_annual_stats_embed(self, year_display: str, avg_duration: float, longest_info: str, ranking_text: str):
        logger.info(f"Creating annual stats embed for {year_display}")

        # Embedを作成し、フィールドを追加
        embed = discord.Embed(title=f"【{year_display}】年間通話統計情報", color=constants.EMBED_COLOR_SUCCESS)
        embed.add_field(name="年間: 平均通話時間", value=f"{formatters.format_duration(avg_duration)}", inline=False)
        embed.add_field(name="年間: 最長通話", value=longest_info, inline=False)
        embed.add_field(name="年間: 通話時間ランキング", value=ranking_text, inline=False)
        logger.debug("Annual stats embed created successfully.")
        # 作成したEmbedを返す
        return embed



    # --- /monthly_stats コマンド ---
    # 月間通話統計を表示するコマンドのコールバック関数
    @app_commands.command(name="monthly_stats", description="月間通話統計を表示します") # nameとdescriptionを明示
    @app_commands.describe(month="表示する年月（形式: YYYY-MM）省略時は今月")
    @app_commands.guild_only()
    async def monthly_stats_callback(self, interaction: discord.Interaction, month: str = None):
        logger.info(f"Received /monthly_stats command from {interaction.user.id} in guild {interaction.guild.id} with month: {month}")
        # 月の指定がなければ現在の月を使用
        if month is None:
            now = datetime.datetime.now(datetime.timezone.utc)
            month = now.strftime("%Y-%m")
            logger.debug(f"Month not specified, using current month: {month}")

        # 指定された月の形式を検証
        try:
            year, mon = month.split("-")
            month_display = f"{year}年{mon}月"
        except ValueError:
            logger.warning(f"Invalid month format received: {month}")
            await interaction.response.send_message("指定された月の形式が正しくありません。形式は YYYY-MM で指定してください。", ephemeral=True)
            return

        # 月間統計Embedを作成
        embed, month_display = await self._create_monthly_stats_embed(interaction.guild, month)

        # Embedが作成できたか確認し、結果を送信
        if embed is None:
            logger.info(f"No monthly stats found for {month_display}")
            await interaction.response.send_message(f"{month_display}は通話統計情報が記録されていません", ephemeral=True)
            return

        await interaction.response.send_message(embed=embed, ephemeral=True)
        logger.info(f"/monthly_stats command executed successfully for {month_display}")

    # --- /total_time コマンド ---
    # 指定したメンバーの総通話時間を表示するコマンドのコールバック関数
    @app_commands.command(name="total_time", description="指定したメンバーの総通話時間を表示します") # nameとdescriptionを明示
    @app_commands.describe(member="通話時間を確認するメンバー（省略時は自分）")
    @app_commands.guild_only()
    async def total_time_callback(self, interaction: discord.Interaction, member: discord.Member = None):
        logger.info(f"Received /total_time command from {interaction.user.id} in guild {interaction.guild.id} for member: {member}")
        # メンバーの指定がなければコマンド実行ユーザーを使用
        member = member or interaction.user
        logger.debug(f"Checking total time for member: {member.id}")
        # メンバーの総通話時間をデータベースから取得
        # データベースエラーはdatabase.py内で処理され、デフォルト値 (0) が返されます。
        total_seconds = await get_total_call_time(member.id)
        logger.debug(f"Total time for {member.id}: {total_seconds} seconds")

        # 結果表示用のEmbedを作成
        embed = discord.Embed(color=constants.EMBED_COLOR_INFO)
        embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)

        # 通話時間に応じて表示内容を分岐
        if total_seconds == 0:
            embed.add_field(name=constants.EMBED_FIELD_TOTAL_CALL_TIME, value=constants.MESSAGE_NO_CALL_HISTORY, inline=False)
            logger.info(f"No call history found for member {member.id}")
        else:
            formatted_time = formatters.format_duration(total_seconds)
            embed.add_field(name=constants.EMBED_FIELD_TOTAL_CALL_TIME, value=formatted_time, inline=False)
            logger.info(f"Total call time for member {member.id}: {formatted_time}")

        # 結果を送信
        await interaction.response.send_message(embed=embed, ephemeral=True)
        logger.info(f"/total_time command executed successfully for member {member.id}")

    # --- /total_call_ranking コマンド ---
    # 総通話時間ランキングを表示するコマンドのコールバック関数
    @app_commands.command(name="call_ranking", description="総通話時間ランキングを表示します") # nameとdescriptionを明示
    @app_commands.guild_only()
    async def call_ranking_callback(self, interaction: discord.Interaction):
        logger.info(f"Received /call_ranking command from {interaction.user.id} in guild {interaction.guild.id}")
        guild = interaction.guild
        members = guild.members
        logger.debug(f"Fetching total call times for {len(members)} members in guild {guild.id}")

        # サーバー内の全メンバーの通話時間をまとめて取得
        member_ids = [member.id for member in members]
        # データベースエラーはdatabase.py内で処理され、空の辞書が返される可能性があります。
        member_call_times = await get_total_call_time_for_guild_members(member_ids)
        logger.debug(f"Fetched total call times for {len(member_call_times)} members.")

        # 通話時間が0より大きいメンバーのみを対象にフィルタリング
        filtered_member_call_times = {
            member_id: total_seconds for member_id, total_seconds in member_call_times.items()
            if total_seconds > 0
        }
        logger.debug(f"Found call times for {len(filtered_member_call_times)} members with > 0 call time.")

        # 通話時間でメンバーを降順にソートしてランキングを作成
        sorted_members = sorted(filtered_member_call_times.items(), key=lambda x: x[1], reverse=True)
        logger.debug(f"Sorted {len(sorted_members)} members for ranking.")

        # ランキングデータがあるか確認し、結果を送信
        if not sorted_members:
            logger.info("No ranking data found.")
            await interaction.response.send_message(constants.MESSAGE_NO_RANKING_DATA, ephemeral=True)
        else:
            # ランキング表示用のEmbedを作成
            embed = discord.Embed(title=constants.EMBED_TITLE_TOTAL_CALL_RANKING, color=constants.EMBED_COLOR_MILESTONE)
            ranking_text = ""
            # メンバーIDからメンバー名を取得するために、ギルドの全メンバーを取得してルックアップを作成
            member_lookup = {member.id: member for member in guild.members}
            # constants.RANKING_LIMIT で定義された上位メンバーのみを表示
            for i, (member_id, total_seconds) in enumerate(sorted_members[:constants.RANKING_LIMIT], start=1):
                member = member_lookup.get(member_id)
                if member:
                    formatted_time = formatters.format_duration(total_seconds)
                    ranking_text += f"{i}. {formatted_time} {member.display_name}\n"
            # ランキング表示件数に制限があることを示すコメントを追加
            if len(sorted_members) > constants.RANKING_LIMIT:
                ranking_text += f"...\n(上位 {constants.RANKING_LIMIT} 名を表示)"
            embed.add_field(name="", value=ranking_text, inline=False)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            logger.info(f"/call_ranking command executed successfully, showing top {min(len(sorted_members), constants.RANKING_LIMIT)}.")

    # --- /call_duration コマンド ---
    # 現在の通話状況を表示するコマンドのコールバック関数
    @app_commands.command(name="call_duration", description="現在の通話状況を表示します") # nameとdescriptionを明示
    @app_commands.guild_only()
    async def call_duration_callback(self, interaction: discord.Interaction):
        logger.info(f"Received /call_duration command from {interaction.user.id} in guild {interaction.guild.id}")
        guild_id = interaction.guild.id
        # voice_state_manager から現在アクティブな通話とその継続時間を取得
        active_calls = self.voice_state_manager.get_active_call_durations(guild_id)
        logger.debug(f"Found {len(active_calls)} active calls in guild {guild_id}")

        # 結果表示用のEmbedを作成
        embed = discord.Embed(color=constants.EMBED_COLOR_INFO)
        embed.set_author(name=constants.EMBED_TITLE_CURRENT_CALL_STATUS)

        # アクティブな通話があるか確認し、結果を送信
        if not active_calls:
            logger.info("No active calls found.")
            await interaction.response.send_message(constants.MESSAGE_NO_ACTIVE_CALLS, ephemeral=True)
        else:
            # 各通話チャンネルと継続時間をEmbedのフィールドに追加
            for call in active_calls:
                channel_id = call['channel_id']
                channel = self.bot.get_channel(channel_id)
                if channel:
                    embed.add_field(name=f"{channel.name}", value=call['duration'], inline=False)
                else:
                    logger.warning(f"Channel with ID {channel_id} not found in bot cache.")
                    embed.add_field(name=f"不明なチャンネル ({channel_id})", value=call['duration'], inline=False)

            await interaction.response.send_message(embed=embed, ephemeral=True)
            logger.info("/call_duration command executed successfully.")

    # --- /help コマンド ---
    # コマンド一覧を表示するコマンドのコールバック関数
    @app_commands.command(name="help", description="コマンド一覧を表示します") # nameとdescriptionを明示
    @app_commands.guild_only()
    async def help_callback(self, interaction: discord.Interaction):
        logger.info(f"Received /help command from {interaction.user.id} in guild {interaction.guild.id}")
        # interaction.client は bot インスタンスを参照します
        # サーバーで利用可能なコマンドリストを取得
        commands = self.bot.tree.get_commands(guild=interaction.guild)
        logger.debug(f"Found {len(commands)} commands for guild {interaction.guild.id}")
        # コマンド一覧表示用のEmbedを作成
        embed = discord.Embed(title=constants.EMBED_TITLE_COMMAND_LIST, color=constants.EMBED_COLOR_SUCCESS)
        # 各コマンドの名前と説明をEmbedのフィールドに追加
        for command in commands:
            embed.add_field(name=command.name, value=command.description, inline=False)
        # 結果を送信
        await interaction.response.send_message(embed=embed, ephemeral=True)
        logger.info("/help command executed successfully.")

    # 管理者用：通知先チャンネル変更コマンド
    # 通知を送信するチャンネルを設定するコマンドのコールバック関数
    @app_commands.command(name="changesendchannel", description="通知を送信するチャンネルを設定します（管理者用）") # nameとdescriptionを明示
    @app_commands.default_permissions(administrator=True) # 管理者権限が必要
    @app_commands.describe(channel="通知を送信するチャンネル")
    @app_commands.guild_only()
    async def changesendchannel_callback(self, interaction: discord.Interaction, channel: discord.TextChannel):
        logger.info(f"Received /changesendchannel command from {interaction.user.id} in guild {interaction.guild.id} with channel: {channel.id}")
        guild_id = interaction.guild.id
        # config から現在の通知チャンネルIDを取得
        current_channel_id = config.get_notification_channel_id(guild_id)
        logger.debug(f"Current notification channel ID for guild {guild_id}: {current_channel_id}")

        # 現在のチャンネルと同じか確認し、結果を送信
        if current_channel_id is not None and current_channel_id == channel.id:
            # interaction.client は bot インスタンスを参照します
            current_channel = self.bot.get_channel(current_channel_id)
            logger.info(f"Notification channel already set to {channel.id}")
            await interaction.response.send_message(constants.MESSAGE_NOTIFICATION_CHANNEL_ALREADY_SET.format(current_channel=current_channel), ephemeral=True)
        else:
            # config を使用して通知チャンネルIDを更新
            config.set_notification_channel_id(guild_id, channel.id)
            logger.info(f"Notification channel set to {channel.id} for guild {guild_id}")
            await interaction.response.send_message(constants.MESSAGE_NOTIFICATION_CHANNEL_SET.format(channel=channel), ephemeral=True)
        logger.info("/changesendchannel command executed successfully.")

    # 管理者用：年間統計情報送信デバッグコマンド
    # 指定した年度の年間通話統計情報を表示するコマンドのコールバック関数
    @app_commands.command(name="debug_annual_stats", description="指定した年度の年間通話統計情報を表示します（管理者用）") # nameとdescriptionを明示
    @app_commands.default_permissions(administrator=True) # 管理者権限が必要
    @app_commands.describe(year="表示する年度（形式: YYYY）。省略時は今年")
    @app_commands.guild_only()
    async def debug_annual_stats_callback(self, interaction: discord.Interaction, year: str = None):
        logger.info(f"Received /debug_annual_stats command from {interaction.user.id} in guild {interaction.guild.id} with year: {year}")
        # 年度の指定がなければ現在の年度を使用
        if year is None:
            now = datetime.datetime.now(ZoneInfo(constants.TIMEZONE_JST))
            year = str(now.year)
        logger.debug(f"Year not specified, using current year: {year}")

        # 年間統計データを取得・処理
        year_display, avg_duration, longest_info, ranking_text, sessions_data, members_total = await self.get_and_process_annual_stats_data(interaction.guild, year)

        # データが取得できたか確認し、結果を送信
        if sessions_data:
            # 年間統計Embedを作成
            embed = await self._create_annual_stats_embed(year_display, avg_duration, longest_info, ranking_text)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            logger.info(f"/debug_annual_stats command executed successfully for year {year}")
        else:
            logger.info(f"No annual stats found for year {year}")
            await interaction.response.send_message(f"{year_display}{constants.MESSAGE_NO_CALL_RECORDS}", ephemeral=True)
        logger.info("/debug_annual_stats command finished.")

    # 管理者用：寝落ち確認設定変更コマンド
    # 寝落ち確認の設定を変更するコマンドのコールバック関数
    @app_commands.command(name="set_sleep_check", description="寝落ち確認の設定を変更します（管理者用）") # nameとdescriptionを明示
    @app_commands.default_permissions(administrator=True) # 管理者権限が必要
    @app_commands.describe(lonely_timeout_minutes="一人以下の状態が続く時間（分単位）", reaction_wait_minutes="反応を待つ時間（分単位）")
    @app_commands.guild_only()
    async def set_sleep_check_callback(self, interaction: discord.Interaction, lonely_timeout_minutes: int = None, reaction_wait_minutes: int = None):
        logger.info(f"Received /set_sleep_check command from {interaction.user.id} in guild {interaction.guild.id} with lonely_timeout_minutes: {lonely_timeout_minutes}, reaction_wait_minutes: {reaction_wait_minutes}")
        # パラメータの指定がない場合は現在の設定を表示
        if lonely_timeout_minutes is None and reaction_wait_minutes is None:
            # データベースエラーが発生した場合、database.py内で処理されるか、呼び出し元に例外が伝播する可能性があります。
            settings = await get_guild_settings(interaction.guild.id)
            logger.info(f"Displaying current sleep check settings for guild {interaction.guild.id}")
            await interaction.response.send_message(
                constants.MESSAGE_CURRENT_SLEEP_CHECK_SETTINGS +
                f"{constants.EMBED_FIELD_LONELY_TIMEOUT}: {settings[constants.COLUMN_LONELY_TIMEOUT_MINUTES]} 分\n" +
                f"{constants.EMBED_FIELD_REACTION_WAIT}: {settings[constants.COLUMN_REACTION_WAIT_MINUTES]} 分",
                ephemeral=True
            )
            return

        # パラメータのバリデーション
        if lonely_timeout_minutes is not None and lonely_timeout_minutes <= 0:
            logger.warning(f"Invalid lonely_timeout_minutes value: {lonely_timeout_minutes}")
            await interaction.response.send_message(constants.MESSAGE_LONELY_TIMEOUT_MIN_ERROR, ephemeral=True)
            return
        if reaction_wait_minutes is not None and reaction_wait_minutes <= 0:
            logger.warning(f"Invalid reaction_wait_minutes value: {reaction_wait_minutes}")
            await interaction.response.send_message(constants.MESSAGE_REACTION_WAIT_MIN_ERROR, ephemeral=True)
            return

        # ギルド設定を更新
        # データベースエラーが発生した場合、database.py内でロールバック処理が行われた後、呼び出し元に例外が伝播する可能性があります。
        await update_guild_settings(interaction.guild.id, lonely_timeout_minutes=lonely_timeout_minutes, reaction_wait_minutes=reaction_wait_minutes)
        logger.info(f"Sleep check settings updated for guild {interaction.guild.id}")
        # 更新後の設定を取得して表示
        # データベースエラーが発生した場合、database.py内で処理されるか、呼び出し元に例外が伝播する可能性があります。
        settings = await get_guild_settings(interaction.guild.id)
        await interaction.response.send_message(
            constants.MESSAGE_SLEEP_CHECK_SETTINGS_UPDATED +
            f"{constants.EMBED_FIELD_LONELY_TIMEOUT}: {settings[constants.COLUMN_LONELY_TIMEOUT_MINUTES]} 分\n" +
            f"{constants.EMBED_FIELD_REACTION_WAIT}: {settings[constants.COLUMN_REACTION_WAIT_MINUTES]} 分",
            ephemeral=True
        )
        logger.info("/set_sleep_check command executed successfully.")

    # --- /get_mute_count コマンド ---
    # 指定したメンバーの自動ミュート回数を表示するコマンドのコールバック関数
    @app_commands.command(name="get_mute_count", description="指定したメンバーの自動ミュート回数を表示します")
    @app_commands.describe(member="ミュート回数を確認するメンバー")
    @app_commands.guild_only()
    async def get_mute_count_callback(self, interaction: discord.Interaction, member: discord.Member):
        logger.info(f"Received /get_mute_count command from {interaction.user.id} in guild {interaction.guild.id} for member: {member.id}")

        try:
            mute_count = await get_mute_count(member.id)
            logger.debug(f"Mute count for member {member.id}: {mute_count}")

            embed = discord.Embed(color=constants.EMBED_COLOR_INFO)
            embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)

            if mute_count == 0:
                embed.description = "まだ自動ミュートされたことはありません。"
            else:
                embed.description = f"自動ミュートされた回数: {mute_count} 回"

            await interaction.response.send_message(embed=embed, ephemeral=True)
            logger.info(f"/get_mute_count command executed successfully for member {member.id}.")

        except Exception as e:
            logger.error(f"An error occurred during /get_mute_count for member {member.id}: {e}")
            await interaction.response.send_message("ミュート回数の取得中にエラーが発生しました。", ephemeral=True)
