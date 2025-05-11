import discord
from discord.ext import tasks
import datetime
from zoneinfo import ZoneInfo
import logging

from database import record_voice_session_to_db
from formatters import format_duration, convert_utc_to_jst
from config import get_notification_channel_id
import constants

# ロガーを取得
logger = logging.getLogger(__name__)

class CallNotificationManager:
    """
    通話開始/終了通知の処理を行います。
    """
    def __init__(self, bot):
        self.bot = bot
        logger.info("CallNotificationManager initialized.")
        # 通話開始時間と最初に通話を開始した人を記録する辞書（通話通知用）
        # キー: (guild_id, voice_channel_id), 値: {"start_time": datetimeオブジェクト, "first_member": member_id}
        self.call_sessions = {}
        logger.debug("Initialized call_sessions dictionary in CallNotificationManager.")

    async def _send_notification_embed(self, guild_id: int, embed: discord.Embed, content: str = None, allowed_mentions: discord.AllowedMentions = None):
        """
        指定されたギルドの通知チャンネルにEmbedを送信します。
        通知チャンネルの取得、存在確認、Embed送信、例外処理を行います。
        """
        notification_channel_id = get_notification_channel_id(guild_id)
        if notification_channel_id:
            notification_channel = self.bot.get_channel(notification_channel_id)
            if notification_channel:
                try:
                    await notification_channel.send(content=content, embed=embed, allowed_mentions=allowed_mentions)
                    logger.info(f"Sent notification to channel {notification_channel_id}.")
                except discord.Forbidden:
                    logger.error(f"Error: Missing send permissions for channel {notification_channel.name} ({notification_channel_id}).")
                except Exception as e:
                    logger.error(f"An error occurred while sending notification: {e}")
            else:
                # 通知チャンネルが見つからない場合のログ出力
                logging.warning(f"Notification channel not found: Guild ID {guild_id}")
        else:
            logger.info(f"Notification channel not set for guild {guild_id}. Notification will not be sent.")

    async def notify_call_start(self, member: discord.Member, channel: discord.VoiceChannel):
        """
        メンバーがボイスチャンネルに参加した際に通話開始通知を送信します。
        """
        logger.info(f"notify_call_start: Member {member.id} joined channel {channel.id} ({channel.name}).")
        guild_id = member.guild.id
        now = datetime.datetime.now(datetime.timezone.utc)
        key = (guild_id, channel.id)
        logger.debug(f"Guild ID: {guild_id}, Channel ID: {channel.id}")

        # ギルド内でそのチャンネルでの通話が開始された最初のメンバーであれば通知を送信します。
        if guild_id not in self.call_sessions:
            self.call_sessions[guild_id] = {}
            logger.debug(f"Created call_sessions entry for guild {guild_id}.")

        # 移動の場合は、移動先チャンネルに誰もいない状態から一人になった場合、または最初から一人で通話に参加した場合に通話開始とみなす
        # 入室の場合は、ギルド内でそのチャンネルでの通話が開始された最初のメンバーであれば通知を送信
        # どちらの場合も、そのチャンネルIDがまだcall_sessionsに記録されていないことが条件
        if channel.id not in self.call_sessions[guild_id]:
             # 新しい通話セッションの開始時刻と最初のメンバーを記録
             start_time = now
             self.call_sessions[guild_id][channel.id] = {"start_time": start_time, "first_member": member.id}
             logger.info(f"Starting new call session in channel {channel.id} ({guild_id}).")
             # JSTに変換して表示用にフォーマット
             jst_time = convert_utc_to_jst(start_time)
             # 通話開始通知用のEmbedを作成
             embed = discord.Embed(title=constants.EMBED_TITLE_CALL_START, color=constants.EMBED_COLOR_CALL_START)
             embed.set_thumbnail(url=f"{member.avatar.url}?size=128")
             embed.add_field(name=constants.EMBED_FIELD_CHANNEL, value=f"{channel.name}")
             embed.add_field(name=constants.EMBED_FIELD_STARTED_BY, value=f"{member.display_name}")
             embed.add_field(name=constants.EMBED_FIELD_START_TIME, value=f"{jst_time.strftime('%Y/%m/%d %H:%M:%S')}")
             # 通知を送信
             await self._send_notification_embed(guild_id, embed, content=constants.MENTION_EVERYONE, allowed_mentions=constants.ALLOWED_MENTIONS_EVERYONE)
        else:
            logger.debug(f"Channel {channel.id} ({guild_id}) has an existing call session. Skipping call start notification.")

    async def notify_call_end(self, guild_id: int, channel: discord.VoiceChannel):
        """
        ボイスチャンネルから全員が退出した際に通話終了通知を送信します。
        """
        logger.info(f"notify_call_end: Channel {channel.id} ({guild_id}) is now empty. Considering call ended.")
        now = datetime.datetime.now(datetime.timezone.utc)
        voice_channel_id = channel.id

        # 退出元のチャンネルでの通話セッションが存在する場合
        if guild_id in self.call_sessions and voice_channel_id in self.call_sessions[guild_id]:
            session = self.call_sessions[guild_id].pop(voice_channel_id) # 通話セッションを終了
            start_time = session["start_time"]
            call_duration = (now - start_time).total_seconds() # 通話時間を計算
            duration_str = format_duration(call_duration) # 表示用にフォーマット
            logger.debug(f"Call duration: {duration_str}")
            # 通話終了通知用のEmbedを作成
            embed = discord.Embed(title=constants.EMBED_TITLE_CALL_END, color=constants.EMBED_COLOR_CALL_END)
            embed.add_field(name="チャンネル", value=f"{channel.name}")
            embed.add_field(name="通話時間", value=f"{duration_str}")
            # 通知を送信
            await self._send_notification_embed(guild_id, embed)
        else:
            logger.debug(f"No active call session in channel {voice_channel_id} ({guild_id}).")


class StatisticalSessionManager:
    """
    統計のための2人以上通話セッションの追跡と記録を行います。
    """
    def __init__(self):
        logger.info("StatisticalSessionManager initialized.")
        # (guild_id, channel_id) をキーに、現在進行中の「2人以上通話セッション」を記録する
        # 値: {"session_start": datetimeオブジェクト, "current_members": {member_id: join_time}, "all_participants": {member_id}}
        # session_start: そのチャンネルで2人以上の通話が開始された時刻
        # current_members: 現在そのチャンネルにいるメンバーとそのチャンネルに参加した時刻
        # all_participants: そのセッション中に一度でもチャンネルに参加した全てのメンバーIDのセット
        self.active_voice_sessions = {}
        logger.debug("Initialized active_voice_sessions dictionary in StatisticalSessionManager.")

    def start_session(self, guild_id: int, channel: discord.VoiceChannel):
        """
        新しい2人以上通話セッションを開始します。
        """
        logger.info(f"Starting new two-or-more-member call session in channel {channel.id} ({guild_id}).")
        now = datetime.datetime.now(datetime.timezone.utc)
        key = (guild_id, channel.id)
        # 新しい2人以上通話セッションを開始
        # セッション開始時刻は、通話がconstants.MIN_MEMBERS_FOR_SESSION人以上になった時刻（この時点の now）
        self.active_voice_sessions[key] = {
            "session_start": now,
            "current_members": { m.id: now for m in channel.members }, # 現在のメンバーとその参加時刻を記録
            "all_participants": set(m.id for m in channel.members) # 全参加者リストに現在のメンバーを追加
        }
        logger.debug(f"Created new active_voice_sessions entry: {key}")

    def update_session_members(self, guild_id: int, channel: discord.VoiceChannel):
        """
        既存の2人以上通話セッションにメンバーの出入りを反映します。
        """
        logger.debug(f"Updating two-or-more-member call session members in channel {channel.id} ({guild_id}).")
        now = datetime.datetime.now(datetime.timezone.utc)
        key = (guild_id, channel.id)
        if key in self.active_voice_sessions:
            session_data = self.active_voice_sessions[key]
            current_member_ids = {m.id for m in channel.members}
            previous_member_ids = set(session_data["current_members"].keys())

            # 新規参加メンバーを追加
            for member_id in current_member_ids - previous_member_ids:
                 session_data["current_members"][member_id] = now # 新規参加メンバーの参加時刻を記録
                 session_data["all_participants"].add(member_id) # 全参加者リストにメンバーを追加
                 logger.debug(f"Added member {member_id} to active_voice_sessions[{key}].")

            # 退出メンバーを current_members から削除し、終了セッションデータを生成
            ended_sessions_data = []
            for member_id in previous_member_ids - current_member_ids:
                if member_id in session_data["current_members"]: # 念のため存在確認
                    join_time = session_data["current_members"].pop(member_id) # メンバーを現在のメンバーリストから削除
                    duration = (now - join_time).total_seconds() # そのメンバーの通話時間を計算
                    ended_sessions_data.append((member_id, duration, join_time)) # 終了リストに追加
                    logger.debug(f"Individual session end data for member {member_id}: Duration {duration}, Join time {join_time}")

            logger.debug(f"Updated active_voice_sessions[{key}]. Current members: {list(session_data['current_members'].keys())}, All participants count: {len(session_data['all_participants'])}")
            return ended_sessions_data # 終了した個別のメンバーセッションデータのリストを返す
        else:
            logger.warning(f"Attempted to update session members for non-existent active session in channel {channel.id} ({guild_id}).")
            return []

    async def end_session(self, guild_id: int, channel: discord.VoiceChannel):
        """
        2人以上通話セッションを終了し、データベースに記録します。
        終了した個別のメンバーセッションデータを返します。
        """
        logger.info(f"Ending two-or-more-member call session for channel {channel.id} ({guild_id}).")
        key = (guild_id, channel.id)
        ended_sessions_data = [] # 終了した個別のメンバーセッションデータを収集するリスト

        # 指定されたチャンネルの2人以上通話セッションがアクティブな場合
        if key in self.active_voice_sessions:
            session_data = self.active_voice_sessions[key]
            now = datetime.datetime.now(datetime.timezone.utc)
            logger.debug(f"Active session found for channel {key}.")

            # セッション終了時の残メンバーの統計更新と通知チェックのためにデータを収集
            remaining_members_data = session_data["current_members"].copy()
            for m_id, join_time in remaining_members_data.items():
                d = (now - join_time).total_seconds()
                ended_sessions_data.append((m_id, d, join_time)) # 終了リストに追加
                logger.debug(f"Data for member {m_id} remaining in ended session: Duration {d}, Join time {join_time}")
                session_data["current_members"].pop(m_id) # 残メンバーを現在のメンバーリストから削除
                logger.debug(f"Removed member {m_id} from active_voice_sessions[{key}]['current_members'].")


            # セッション全体の通話時間を計算し、データベースに記録
            overall_duration = (now - session_data["session_start"]).total_seconds()
            logger.info(f"Recording overall two-or-more-member call session for channel {channel.id} ({guild_id}). Start time: {session_data['session_start']}, Duration: {overall_duration}, All participants: {list(session_data['all_participants'])}")
            # TODO: データベース操作のエラーハンドリングを追加する（例: try...except）
            await record_voice_session_to_db(session_data["session_start"], overall_duration, list(session_data["all_participants"]))
            self.active_voice_sessions.pop(key, None) # アクティブセッションから削除
            logger.debug(f"Removed channel {key} from active_voice_sessions.")

        else:
            logger.warning(f"No active two-or-more-member call session found for channel {channel.id} ({guild_id}). Skipping end process.")

        return ended_sessions_data # 終了した個別のメンバーセッションデータのリストを返す

    def get_active_session_keys(self):
        """
        現在アクティブな2人以上通話セッションのキー (guild_id, channel_id) のリストを返します。
        """
        return list(self.active_voice_sessions.keys())

    def get_session_start_time(self, guild_id: int, channel_id: int):
        """
        指定されたチャンネルのアクティブなセッション開始時刻を返します。
        セッションが存在しない場合は None を返します。
        """
        key = (guild_id, channel_id)
        if key in self.active_voice_sessions:
            return self.active_voice_sessions[key]["session_start"]
        return None

    def is_session_active(self, guild_id: int, channel_id: int):
        """
        指定されたチャンネルで2人以上通話セッションがアクティブかどうかを返します。
        """
        return (guild_id, channel_id) in self.active_voice_sessions

    def get_formatted_active_sessions(self, guild_id: int):
        """
        指定されたギルドのアクティブな2人以上通話チャンネルとその通話時間を取得し、
        表示用にフォーマットして返します。
        """
        logger.info(f"Fetching formatted active call durations for guild {guild_id} from StatisticalSessionManager.")
        active_calls = []
        now = datetime.datetime.now(datetime.timezone.utc)
        # アクティブな2人以上通話セッションを全て確認
        for key, session_data in self.active_voice_sessions.items():
            # 指定されたギルドのセッションのみを対象とする
            if key[0] == guild_id:
                # セッション開始からの経過時間を計算
                duration_seconds = (now - session_data["session_start"]).total_seconds()
                # 表示用にフォーマット
                formatted_duration = format_duration(duration_seconds)
                # チャンネルIDとフォーマット済み通話時間をリストに追加
                active_calls.append({"channel_id": key[1], "duration": formatted_duration})
                logger.debug(f"Active call channel ID: {key[1]}, Duration: {formatted_duration}")
        logger.info(f"Number of active call channels for guild {guild_id}: {len(active_calls)}")
        # アクティブな通話チャンネルIDとその通話時間のリストを返す
        return active_calls


class BotStatusUpdater:
    """
    ボットのステータスを現在アクティブな通話チャンネルに基づいて更新します。
    """
    def __init__(self, bot, statistical_session_manager: StatisticalSessionManager):
        self.bot = bot
        self.statistical_session_manager = statistical_session_manager
        logger.info("BotStatusUpdater initialized.")
        # 2人以上が通話中のチャンネルを追跡するセット
        # 要素: (guild_id, channel_id)
        self.active_status_channels = set()
        logger.debug("Initialized active_status_channels set in BotStatusUpdater.")

        # ボットのステータスを通話時間で更新するタスク
        self.update_call_status_task = tasks.loop(seconds=constants.STATUS_UPDATE_INTERVAL_SECONDS)(self._update_call_status_task)
        logger.debug(f"Status update task set to run every {constants.STATUS_UPDATE_INTERVAL_SECONDS} seconds.")

    def add_active_channel(self, guild_id: int, channel_id: int):
        """
        ステータス更新の対象となるアクティブチャンネルを追加します。
        """
        key = (guild_id, channel_id)
        if key not in self.active_status_channels:
            self.active_status_channels.add(key)
            logger.debug(f"Added channel {key} to active_status_channels.")
            # 初めて2人以上の通話が始まった場合、ボットのステータス更新タスクを開始
            if not self.update_call_status_task.is_running():
                self.update_call_status_task.start()
                logger.info("Started bot status update task.")

    def remove_active_channel(self, guild_id: int, channel_id: int):
        """
        ステータス更新の対象からアクティブチャンネルを削除します。
        """
        key = (guild_id, channel_id)
        if key in self.active_status_channels:
            self.active_status_channels.discard(key)
            logger.debug(f"Removed channel {key} from active_status_channels.")
            # 2人以上の通話がすべて終了した場合、ボットのステータス更新タスクを停止しステータスをクリア
            if not self.active_status_channels and self.update_call_status_task.is_running():
                self.update_call_status_task.stop()
                # ステータスはタスク内でクリアされるため、ここではクリアしない
                logger.info("No active two-or-more-member call channels remaining, stopping status update task.")


    async def _update_call_status_task(self):
        """
        ボットのステータスを現在アクティブな2人以上通話チャンネルの通話時間で更新するタスク。
        constants.STATUS_UPDATE_INTERVAL_SECONDS 間隔で実行されます。
        """
        logger.debug("Executing status update task.")
        # 2人以上通話中のチャンネルがあるか確認
        if self.active_status_channels:
            logger.debug(f"Active status channels found: {self.active_status_channels}")
            # active_status_channelsからステータスに表示するチャンネルを一つ選択（セットなので順序は保証されない）
            channel_key_to_display = next(iter(self.active_status_channels))
            guild_id, channel_id = channel_key_to_display
            guild = self.bot.get_guild(guild_id)
            channel = self.bot.get_channel(channel_id)

            # ギルド、チャンネルが存在し、かつそのチャンネルがまだアクティブセッションリストにあるか確認
            if guild and channel and self.statistical_session_manager.is_session_active(guild_id, channel_id):
                logger.debug(f"Channel to display in status: {channel.name} ({guild.name})")
                # 選択したチャンネルの通話時間を計算
                session_start_time = self.statistical_session_manager.get_session_start_time(guild_id, channel_id)
                if session_start_time:
                    duration_seconds = (datetime.datetime.now(datetime.timezone.utc) - session_start_time).total_seconds()
                    # 表示用にフォーマット
                    formatted_duration = format_duration(duration_seconds)
                    # ボットのカスタムステータスを設定
                    activity = discord.CustomActivity(name=f"{channel.name}: {formatted_duration}")
                    await self.bot.change_presence(activity=activity)
                    logger.info(f"Updated bot status: {activity.name}")
                else:
                     logger.warning(f"Session start time not found for active status channel {channel_key_to_display}.")
                     # セッション開始時間がない場合はactive_status_channelsから削除
                     self.active_status_channels.discard(channel_key_to_display)
                     logger.debug(f"Removed channel {channel_key_to_display} from active_status_channels due to missing session start time.")
                     if not self.active_status_channels:
                         await self.bot.change_presence(activity=None)
                         logger.info("No active status channels remaining after removing invalid entry, clearing status.")

            else:
                logger.warning(f"Status display target channel {channel_key_to_display} not found or not in active sessions.")
                # チャンネルが見つからない、またはactive_voice_sessionsにない場合は
                # active_status_channels から削除し、2人以上通話がすべて終了していればステータスをクリア
                self.active_status_channels.discard(channel_key_to_display)
                logger.debug(f"Removed channel {channel_key_to_display} from active_status_channels.")
                if not self.active_status_channels:
                     await self.bot.change_presence(activity=None)
                     logger.info("No active status channels remaining, clearing status.")
        else:
            logger.debug("No active status channels. Clearing status.")
            # 2人以上の通話がない場合はボットのステータスをクリア
            await self.bot.change_presence(activity=None)
            logger.info("Cleared bot status.")


class VoiceStateManager:
    """
    ボイスチャンネルの状態変化を調整し、各責任を持つコンポーネントに処理を委譲します。
    """
    def __init__(self, bot, call_notification_manager: CallNotificationManager, statistical_session_manager: StatisticalSessionManager, bot_status_updater: BotStatusUpdater):
        self.bot = bot
        self.call_notification_manager = call_notification_manager
        self.statistical_session_manager = statistical_session_manager
        self.bot_status_updater = bot_status_updater
        logger.info("VoiceStateManager initialized with decomposed components.")

    # --- ボイスステート更新通知ハンドラ ---
    async def notify_member_joined(self, member: discord.Member, channel_after: discord.VoiceChannel):
        """
        メンバーがボイスチャンネルに参加したことをVoiceStateManagerに通知します。
        各コンポーネントに処理を委譲します。
        """
        logger.info(f"notify_member_joined: Member {member.id} joined channel {channel_after.id} ({channel_after.name}).")
        guild_id = member.guild.id
        key = (guild_id, channel_after.id)

        # 通話通知マネージャーに処理を委譲
        # チャンネルに一人だけになった場合、または最初から一人で参加した場合に通話開始通知を送信
        if len(channel_after.members) == 1:
             await self.call_notification_manager.notify_call_start(member, channel_after)
        # 移動の場合は、移動先チャンネルに誰もいない状態から一人になった場合に通話開始通知を送信
        # notify_member_moved で別途処理するため、ここでは len(channel_after.members) == 1 の場合のみ考慮

        # 統計セッションマネージャーに処理を委譲
        # チャンネル内の人数がconstants.MIN_MEMBERS_FOR_SESSION (現在2) 人以上の場合に、
        # 新しいセッションを開始するか、既存のセッションにメンバーを追加します。
        if len(channel_after.members) >= constants.MIN_MEMBERS_FOR_SESSION:
            if not self.statistical_session_manager.is_session_active(guild_id, channel_after.id):
                self.statistical_session_manager.start_session(guild_id, channel_after)
            else:
                # 既存セッションへのメンバー追加は update_session_members でまとめて行う
                pass # ここでは何もしない
            # ステータス更新対象としてチャンネルを追加
            self.bot_status_updater.add_active_channel(guild_id, channel_after.id)
        else:
             # 人数がconstants.MIN_MEMBERS_FOR_SESSION人未満の場合は、既にセッションが存在する場合のみ更新する
             # （一時的に人数が減ってもセッション自体は継続しているとみなす）
             if self.statistical_session_manager.is_session_active(guild_id, channel_after.id):
                 pass # 既存セッションへのメンバー追加は update_session_members でまとめて行う
             # 人数がconstants.MIN_MEMBERS_FOR_SESSION人未満になったら active_status_channels から削除
             # notify_member_left/moved で処理するため、ここでは追加のみ考慮

        # 統計セッションマネージャーでメンバーリストを更新し、終了した個別のセッションデータを取得
        # セッションがアクティブな場合のみ update_session_members を呼び出す
        ended_sessions_data = []
        if self.statistical_session_manager.is_session_active(guild_id, channel_after.id):
             ended_sessions_data = self.statistical_session_manager.update_session_members(guild_id, channel_after)

        # 終了した個別のメンバーセッションデータを返す（voice_events.py で統計更新と通知チェックを行う）
        return ended_sessions_data


    async def notify_member_left(self, member: discord.Member, channel_before: discord.VoiceChannel):
        """
        メンバーがボイスチャンネルから退出したことをVoiceStateManagerに通知します。
        各コンポーネントに処理を委譲します。
        終了した個別のメンバーセッションデータを返します。
        """
        logger.info(f"notify_member_left: Member {member.id} left channel {channel_before.id} ({channel_before.name}).")
        guild_id = member.guild.id
        key = (guild_id, channel_before.id)

        # 通話通知マネージャーに処理を委譲
        # 退出元のチャンネルに誰もいなくなった場合のみ通話終了とみなし、通知を送信
        if len(channel_before.members) == 0:
            await self.call_notification_manager.notify_call_end(guild_id, channel_before)

        # 統計セッションマネージャーに処理を委譲
        ended_sessions_data = [] # 終了した個別のメンバーセッションデータを収集するリスト
        # 退出元のチャンネルで2人以上通話セッションがアクティブな場合
        if self.statistical_session_manager.is_session_active(guild_id, channel_before.id):
            # もし退室後、チャンネル内人数がconstants.MIN_MEMBERS_FOR_SESSION (現在2) 人未満になったら
            # そのチャンネルでの2人以上通話セッション全体を終了する処理を実施
            if len(channel_before.members) < constants.MIN_MEMBERS_FOR_SESSION:
                ended_sessions_data = await self.statistical_session_manager.end_session(guild_id, channel_before)
                # ステータス更新対象からチャンネルを削除
                self.bot_status_updater.remove_active_channel(guild_id, channel_before.id)
            else:
                # 既存セッションからのメンバー削除は update_session_members でまとめて行う
                ended_sessions_data = self.statistical_session_manager.update_session_members(guild_id, channel_before)

        # 終了した個別のメンバーセッションデータを返す（voice_events.py で統計更新と通知チェックを行う）
        return ended_sessions_data


    async def notify_member_moved(self, member: discord.Member, channel_before: discord.VoiceChannel, channel_after: discord.VoiceChannel):
        """
        メンバーがボイスチャンネル間を移動したことをVoiceStateManagerに通知します。
        各コンポーネントに処理を委譲します。
        移動元チャンネルで終了した個別のメンバーセッションデータと、
        移動先チャンネルに参加したメンバーのデータを返します。
        """
        logger.info(f"notify_member_moved: Member {member.id} moved from channel {channel_before.id} ({channel_before.name}) to channel {channel_after.id} ({channel_after.name}).")
        guild_id = member.guild.id
        key_before = (guild_id, channel_before.id)
        key_after = (guild_id, channel_after.id)

        # 通話通知マネージャーに処理を委譲 (移動元からの退出処理)
        # 移動元のチャンネルに誰もいなくなった場合のみ通話終了とみなし、通知を送信
        if len(channel_before.members) == 0:
            await self.call_notification_manager.notify_call_end(guild_id, channel_before)

        # 通話通知マネージャーに処理を委譲 (移動先への入室処理)
        # 移動先のチャンネルに誰もいない状態から一人になった場合に通話開始とみなす
        if len(channel_after.members) == 1:
             await self.call_notification_manager.notify_call_start(member, channel_after)


        # 統計セッションマネージャーに処理を委譲 (移動元からの退出処理)
        ended_sessions_from_before = [] # 移動元チャンネルで終了した個別のメンバーセッションデータを収集するリスト
        if self.statistical_session_manager.is_session_active(guild_id, channel_before.id):
            # もし移動元チャンネルの人数がconstants.MIN_MEMBERS_FOR_SESSION (現在2) 人未満になったら
            # そのチャンネルでの2人以上通話セッション全体を終了する処理を実施
            if len(channel_before.members) < constants.MIN_MEMBERS_FOR_SESSION:
                ended_sessions_from_before = await self.statistical_session_manager.end_session(guild_id, channel_before)
                # ステータス更新対象からチャンネルを削除
                self.bot_status_updater.remove_active_channel(guild_id, channel_before.id)
            else:
                # 既存セッションからのメンバー削除は update_session_members でまとめて行う
                ended_sessions_from_before = self.statistical_session_manager.update_session_members(guild_id, channel_before)


        # 統計セッションマネージャーに処理を委譲 (移動先への入室処理)
        joined_session_data = None # 移動先チャンネルに参加したメンバーのデータ
        # 移動先チャンネルの人数がconstants.MIN_MEMBERS_FOR_SESSION (現在2) 人以上の場合
        if len(channel_after.members) >= constants.MIN_MEMBERS_FOR_SESSION:
            if not self.statistical_session_manager.is_session_active(guild_id, channel_after.id):
                self.statistical_session_manager.start_session(guild_id, channel_after)
            # ステータス更新対象としてチャンネルを追加
            self.bot_status_updater.add_active_channel(guild_id, channel_after.id)

            # 統計セッションマネージャーでメンバーリストを更新し、終了した個別のセッションデータを取得
            # 移動してきたメンバーのデータとしてID、現在の通話時間（この時点では0）、参加時刻を記録
            # update_session_members は退出メンバーのデータを返すため、ここでは別途処理が必要
            now = datetime.datetime.now(datetime.timezone.utc)
            if member.id not in self.statistical_session_manager.active_voice_sessions[key_after]["current_members"]:
                 # 新規参加メンバーの参加時刻を記録
                 self.statistical_session_manager.active_voice_sessions[key_after]["current_members"][member.id] = now
                 self.statistical_session_manager.active_voice_sessions[key_after]["all_participants"].add(member.id)
                 joined_session_data = (member.id, 0, now) # 移動してきたメンバーのデータとして記録
                 logger.debug(f"Recorded joined_session_data for moved member {member.id}.")
            else:
                 # 既に current_members にいる場合は、移動ではなく状態変化とみなす（ここでは処理しない）
                 pass

        else:
            # 移動先チャンネルの人数がconstants.MIN_MEMBERS_FOR_SESSION人未満の場合は、既にセッションが存在する場合のみ更新する
            # （一時的に人数が減ってもセッション自体は継続しているとみなす）
            if self.statistical_session_manager.is_session_active(guild_id, channel_after.id):
                 # 統計セッションマネージャーでメンバーリストを更新し、終了した個別のセッションデータを取得
                 # 移動してきたメンバーのデータとしてID、現在の通話時間（この時点では0）、参加時刻を記録
                 # update_session_members は退出メンバーのデータを返すため、ここでは別途処理が必要
                 now = datetime.datetime.now(datetime.timezone.utc)
                 key_after = (guild_id, channel_after.id)
                 if key_after in self.statistical_session_manager.active_voice_sessions:
                     if member.id not in self.statistical_session_manager.active_voice_sessions[key_after]["current_members"]:
                         # 新規参加メンバーの参加時刻を記録
                         self.statistical_session_manager.active_voice_sessions[key_after]["current_members"][member.id] = now
                         self.statistical_session_manager.active_voice_sessions[key_after]["all_participants"].add(member.id)
                         joined_session_data = (member.id, 0, now) # 移動してきたメンバーのデータとして記録
                         logger.debug(f"Recorded joined_session_data for moved member {member.id}.")
                     else:
                         # 既に current_members にいる場合は、移動ではなく状態変化とみなす（ここでは処理しない）
                         pass


        # 移動元チャンネルで終了した個別のメンバーセッションデータのリストと、
        # 移動先チャンネルに参加したメンバーのデータを返す
        return ended_sessions_from_before, joined_session_data

    # 二人以上の通話時間計算ヘルパー関数は StatisticalSessionManager に移動
    # get_active_call_durations は StatisticalSessionManager に移動
    # _update_call_status_task は BotStatusUpdater に移動
    # record_session_end は StatisticalSessionManager に移動

    # get_active_call_durations を StatisticalSessionManager から呼び出すためのラッパー
    def get_active_call_durations(self, guild_id: int):
        """
        指定されたギルドのアクティブな2人以上通話チャンネルとその通話時間を取得し、
        表示用にフォーマットして返します。/call_duration コマンドで使用されます。
        StatisticalSessionManager に処理を委譲します。
        """
        logger.info(f"Fetching active call durations for guild {guild_id} via VoiceStateManager.")
        # StatisticalSessionManager の新しいメソッドを呼び出すように変更
        return self.statistical_session_manager.get_formatted_active_sessions(guild_id)
