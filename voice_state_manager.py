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

class VoiceStateManager:
    """
    ボイスチャンネルの状態管理、通話セッションの記録、通話通知、
    および2人以上の通話状態の追跡とステータス更新を行います。
    """
    def __init__(self, bot):
        self.bot = bot
        logger.info("VoiceStateManager initialized.")
        # 通話開始時間と最初に通話を開始した人を記録する辞書（通話通知用）
        # キー: (guild_id, voice_channel_id), 値: {"start_time": datetimeオブジェクト, "first_member": member_id}
        self.call_sessions = {}
        logger.debug("Initialized call_sessions dictionary.")

        # (guild_id, channel_id) をキーに、現在進行中の「2人以上通話セッション」を記録する
        # 値: {"session_start": datetimeオブジェクト, "current_members": {member_id: join_time}, "all_participants": {member_id}}
        # session_start: そのチャンネルで2人以上の通話が開始された時刻
        # current_members: 現在そのチャンネルにいるメンバーとそのチャンネルに参加した時刻
        # all_participants: そのセッション中に一度でもチャンネルに参加した全てのメンバーIDのセット
        self.active_voice_sessions = {}
        logger.debug("Initialized active_voice_sessions dictionary.")

        # 2人以上が通話中のチャンネルを追跡するセット
        # 要素: (guild_id, channel_id)
        self.active_status_channels = set()
        logger.debug("Initialized active_status_channels set.")

        # ボットのステータスを通話時間で更新するタスク
        self.update_call_status_task = tasks.loop(seconds=constants.STATUS_UPDATE_INTERVAL_SECONDS)(self._update_call_status_task)
        logger.debug(f"Status update task set to run every {constants.STATUS_UPDATE_INTERVAL_SECONDS} seconds.")

    # --- ボイスステート更新ハンドラ ---
    async def handle_member_join(self, member: discord.Member, channel_after: discord.VoiceChannel):
        """
        メンバーがボイスチャンネルに参加した時に呼び出されるハンドラ。
        通話通知機能と2人以上通話状態の記録を処理します。
        """
        logger.info(f"handle_member_join: Member {member.id} joined channel {channel_after.id} ({channel_after.name}).")
        guild_id = member.guild.id
        now = datetime.datetime.now(datetime.timezone.utc)
        key = (guild_id, channel_after.id)
        logger.debug(f"Guild ID: {guild_id}, Channel ID: {channel_after.id}")

        # 通話通知機能 (入室時)
        # ギルド内でそのチャンネルでの通話が開始された最初のメンバーであれば通知を送信します。
        if guild_id not in self.call_sessions:
            self.call_sessions[guild_id] = {}
            logger.debug(f"Created call_sessions entry for guild {guild_id}.")
        if channel_after.id not in self.call_sessions[guild_id]:
             # 新しい通話セッションの開始時刻と最初のメンバーを記録
             start_time = now
             self.call_sessions[guild_id][channel_after.id] = {"start_time": start_time, "first_member": member.id}
             logger.info(f"Starting new call session in channel {channel_after.id} ({guild_id}).")
             # JSTに変換して表示用にフォーマット
             jst_time = convert_utc_to_jst(start_time)
             # 通話開始通知用のEmbedを作成
             embed = discord.Embed(title=constants.EMBED_TITLE_CALL_START, color=constants.EMBED_COLOR_CALL_START)
             embed.set_thumbnail(url=f"{member.avatar.url}?size=128")
             embed.add_field(name=constants.EMBED_FIELD_CHANNEL, value=f"{channel_after.name}")
             embed.add_field(name=constants.EMBED_FIELD_STARTED_BY, value=f"{member.display_name}")
             embed.add_field(name=constants.EMBED_FIELD_START_TIME, value=f"{jst_time.strftime('%Y/%m/%d %H:%M:%S')}")
             # 通知チャンネルを取得し、通知を送信
             notification_channel_id = get_notification_channel_id(guild_id)
             if notification_channel_id:
                 notification_channel = self.bot.get_channel(notification_channel_id)
                 if notification_channel:
                     try:
                         await notification_channel.send(content=constants.MENTION_EVERYONE, embed=embed, allowed_mentions=constants.ALLOWED_MENTIONS_EVERYONE)
                         logger.info(f"Sent call start notification to channel {notification_channel_id}.")
                     except discord.Forbidden:
                         logger.error(f"Error: Missing send permissions for channel {notification_channel.name} ({notification_channel_id}).")
                     except Exception as e:
                         logger.error(f"An error occurred while sending call start notification: {e}")
                 else:
                     # 通知チャンネルが見つからない場合のログ出力
                     logging.warning(f"Notification channel not found: Guild ID {guild_id}")
             else:
                 logger.info(f"Notification channel not set for guild {guild_id}. Call start notification will not be sent.")
        else:
            logger.debug(f"Channel {channel_after.id} ({guild_id}) has an existing call session or member count is not 1. Skipping call start notification.")


        # 2人以上通話状態の記録（各メンバーごとに個別記録＋全参加者リストを維持する処理）
        # チャンネル内の人数がconstants.MIN_MEMBERS_FOR_SESSION (現在2) 人以上の場合に、
        # 新しいセッションを開始するか、既存のセッションにメンバーを追加します。
        if len(channel_after.members) >= constants.MIN_MEMBERS_FOR_SESSION:
            logger.debug(f"Channel {channel_after.id} ({guild_id}) member count is {constants.MIN_MEMBERS_FOR_SESSION} or more ({len(channel_after.members)}).")
            if key not in self.active_voice_sessions:
                logger.info(f"Starting new two-or-more-member call session in channel {channel_after.id} ({guild_id}).")
                # 新しい2人以上通話セッションを開始
                # セッション開始時刻は、通話がconstants.MIN_MEMBERS_FOR_SESSION人以上になった時刻（この時点の now）
                self.active_voice_sessions[key] = {
                    "session_start": now,
                    "current_members": { m.id: now for m in channel_after.members }, # 現在のメンバーとその参加時刻を記録
                    "all_participants": set(m.id for m in channel_after.members) # 全参加者リストに現在のメンバーを追加
                }
                logger.debug(f"Created new active_voice_sessions entry: {key}")
            else:
                logger.debug(f"Destination channel {channel_after.id} ({guild_id}) has an existing two-or-more-member call session. Updating member list.")
                # 既存の2人以上通話セッションがある場合、新たに入室したメンバーを更新する
                session_data = self.active_voice_sessions[key]
                for m in channel_after.members:
                    if m.id not in session_data["current_members"]:
                        session_data["current_members"][m.id] = now # 新規参加メンバーの参加時刻を記録
                        logger.debug(f"Added member {m.id} to active_voice_sessions[{key}]['current_members'].")
                    session_data["all_participants"].add(m.id) # 全参加者リストにメンバーを追加
                    logger.debug(f"Added member {m.id} to active_voice_sessions[{key}]['all_participants'].")


            # チャンネルの人数がconstants.MIN_MEMBERS_FOR_SESSION人以上になったら active_status_channels に追加
            if key not in self.active_status_channels:
                self.active_status_channels.add(key)
                logger.debug(f"Added channel {key} to active_status_channels.")
                # 初めて2人以上の通話が始まった場合、ボットのステータス更新タスクを開始
                if not self.update_call_status_task.is_running():
                    self.update_call_status_task.start()
                    logger.info("Started bot status update task.")

        else:
            logger.debug(f"Destination channel {channel_after.id} ({guild_id}) member count is less than {constants.MIN_MEMBERS_FOR_SESSION} ({len(channel_after.members)}).")
            # 人数がconstants.MIN_MEMBERS_FOR_SESSION人未満の場合は、既にセッションが存在する場合のみ更新する
            # （一時的に人数が減ってもセッション自体は継続しているとみなす）
            if key in self.active_voice_sessions:
                logger.debug(f"Destination channel {channel_after.id} ({guild_id}) has an existing two-or-more-member call session. Updating member list.")
                session_data = self.active_voice_sessions[key]
                for m in channel_after.members:
                    if m.id not in session_data["current_members"]:
                        session_data["current_members"][m.id] = now # 新規参加メンバーの参加時刻を記録
                        logger.debug(f"Added member {m.id} to active_voice_sessions[{key}]['current_members'].")
                    session_data["all_participants"].add(m.id) # 全参加者リストにメンバーを追加
                    logger.debug(f"Added member {m.id} to active_voice_sessions[{key}]['all_participants'].")
            else:
                logger.debug(f"No active two-or-more-member call session in destination channel {channel_after.id} ({guild_id}).")


    async def handle_member_leave(self, member: discord.Member, channel_before: discord.VoiceChannel):
        """
        メンバーがボイスチャンネルから退出した時に呼び出されるハンドラ。
        通話通知機能と2人以上通話状態の記録を処理します。
        """
        logger.info(f"handle_member_leave: Member {member.id} left channel {channel_before.id} ({channel_before.name}).")
        guild_id = member.guild.id
        now = datetime.datetime.now(datetime.timezone.utc)
        key = (guild_id, channel_before.id)
        logger.debug(f"Guild ID: {guild_id}, Channel ID: {channel_before.id}")

        # 通話通知機能 (退出時)
        voice_channel_before_id = channel_before.id
        # 退出元のチャンネルでの通話セッションが存在する場合
        if guild_id in self.call_sessions and voice_channel_before_id in self.call_sessions[guild_id]:
            voice_channel = channel_before
            # 退出元のチャンネルに誰もいなくなった場合のみ通話終了とみなし、通知を送信
            if len(voice_channel.members) == 0:
                logger.info(f"Channel {voice_channel_before_id} ({guild_id}) is now empty. Considering call ended.")
                session = self.call_sessions[guild_id].pop(voice_channel_before_id) # 通話セッションを終了
                start_time = session["start_time"]
                call_duration = (now - start_time).total_seconds() # 通話時間を計算
                duration_str = format_duration(call_duration) # 表示用にフォーマット
                logger.debug(f"Call duration: {duration_str}")
                # 通話終了通知用のEmbedを作成
                embed = discord.Embed(title=constants.EMBED_TITLE_CALL_END, color=constants.EMBED_COLOR_CALL_END)
                embed.add_field(name="チャンネル", value=f"{voice_channel.name}")
                embed.add_field(name="通話時間", value=f"{duration_str}")
                # 通知チャンネルを取得し、通知を送信
                notification_channel_id = get_notification_channel_id(guild_id)
                if notification_channel_id:
                    notification_channel = self.bot.get_channel(notification_channel_id)
                    if notification_channel:
                        try:
                            await notification_channel.send(embed=embed)
                            logger.info(f"Sent call end notification to channel {notification_channel_id}.")
                        except discord.Forbidden:
                            logger.error(f"Error: Missing send permissions for channel {notification_channel.name} ({notification_channel_id}).")
                        except Exception as e:
                            logger.error(f"An error occurred while sending call end notification: {e}")
                    else:
                        # 通知チャンネルが見つからない場合のログ出力
                        logging.warning(f"Notification channel not found: Guild ID {guild_id}")
                else:
                    logger.info(f"Notification channel not set for guild {guild_id}. Call end notification will not be sent.")
            else:
                logger.debug(f"Channel {voice_channel_before_id} ({guild_id}) still has members ({len(voice_channel.members)}). Not considering call ended.")
        else:
            logger.debug(f"No active call session in channel {voice_channel_before_id} ({guild_id}).")


        # 2人以上通話状態の記録（各メンバーごとに個別記録＋全参加者リストを維持する処理）
        # 退出元のチャンネルで2人以上通話セッションがアクティブな場合
        ended_sessions_data = [] # 終了した個別のメンバーセッションデータを収集するリスト
        if key in self.active_voice_sessions:
            logger.debug(f"Active two-or-more-member call session in channel {channel_before.id} ({guild_id}).")
            session_data = self.active_voice_sessions[key]

            # もし対象メンバーが現在セッションに在室中ならその個人分の退室処理を実施
            if member.id in session_data["current_members"]:
                logger.debug(f"Member {member.id} found in active_voice_sessions[{key}]['current_members']. Ending individual session.")
                join_time = session_data["current_members"].pop(member.id) # メンバーを現在のメンバーリストから削除
                duration = (now - join_time).total_seconds() # そのメンバーの通話時間を計算
                ended_sessions_data.append((member.id, duration, join_time)) # 終了リストに追加
                logger.debug(f"Individual session end data for member {member.id}: Duration {duration}, Join time {join_time}")
            else:
                logger.debug(f"Member {member.id} was not found in active_voice_sessions[{key}]['current_members'].")


            # もし退室後、チャンネル内人数がconstants.MIN_MEMBERS_FOR_SESSION (現在2) 人未満になったら
            # そのチャンネルでの2人以上通話セッション全体を終了する処理を実施
            if channel_before is not None and len(channel_before.members) < constants.MIN_MEMBERS_FOR_SESSION:
                logger.info(f"Channel {channel_before.id} ({guild_id}) member count dropped below {constants.MIN_MEMBERS_FOR_SESSION} ({len(channel_before.members)}). Ending two-or-more-member call session.")
                # セッション終了時の残メンバーの統計更新と通知チェック（voice_events.pyで処理）
                remaining_members_data = session_data["current_members"].copy()
                for m_id, join_time in remaining_members_data.items():
                    d = (now - join_time).total_seconds()
                    ended_sessions_data.append((m_id, d, join_time)) # 終了リストに追加
                    logger.debug(f"Data for member {m_id} remaining in ended session: Duration {d}, Join time {join_time}")
                    session_data["current_members"].pop(m_id) # 残メンバーを現在のメンバーリストから削除

                # セッション全体の通話時間を計算し、データベースに記録
                overall_duration = (now - session_data["session_start"]).total_seconds()
                logger.info(f"Recording overall two-or-more-member call session for channel {channel_before.id} ({guild_id}). Start time: {session_data['session_start']}, Duration: {overall_duration}, All participants: {list(session_data['all_participants'])}")
                await record_voice_session_to_db(session_data["session_start"], overall_duration, list(session_data["all_participants"]))
                self.active_voice_sessions.pop(key, None) # アクティブセッションから削除
                logger.debug(f"Removed channel {key} from active_voice_sessions.")

                # チャンネルの人数がconstants.MIN_MEMBERS_FOR_SESSION人未満になったら active_status_channels から削除
                self.active_status_channels.discard(key)
                logger.debug(f"Removed channel {key} from active_status_channels.")
                # 2人以上の通話がすべて終了した場合、ボットのステータス更新タスクを停止しステータスをクリア
                if not self.active_status_channels and self.update_call_status_task.is_running():
                    self.update_call_status_task.stop()
                    await self.bot.change_presence(activity=None)
                    logger.info("No active two-or-more-member call channels remaining, stopping status update task and clearing status.")
            else:
                logger.debug(f"Channel {channel_before.id} ({guild_id}) still has {constants.MIN_MEMBERS_FOR_SESSION} or more members ({len(channel_before.members)}). Two-or-more-member call session continues.")

        return ended_sessions_data # 終了した個別のメンバーセッションデータのリストを返す

    async def handle_member_move(self, member: discord.Member, channel_before: discord.VoiceChannel, channel_after: discord.VoiceChannel):
        """
        メンバーがボイスチャンネル間を移動した時に呼び出されるハンドラ。
        移動元チャンネルからの退出処理と移動先チャンネルへの入室処理を組み合わせます。
        """
        logger.info(f"handle_member_move: Member {member.id} moved from channel {channel_before.id} ({channel_before.name}) to channel {channel_after.id} ({channel_after.name}).")
        guild_id = member.guild.id
        now = datetime.datetime.now(datetime.timezone.utc)
        key_before = (guild_id, channel_before.id)
        key_after = (guild_id, channel_after.id)
        logger.debug(f"Guild ID: {guild_id}, Source Channel ID: {channel_before.id}, Destination Channel ID: {channel_after.id}")

        # 通話通知機能 (移動元からの退出処理)
        voice_channel_before_id = channel_before.id
        # 移動元のチャンネルでの通話セッションが存在する場合
        if guild_id in self.call_sessions and voice_channel_before_id in self.call_sessions[guild_id]:
            voice_channel = channel_before
            # 移動元のチャンネルに誰もいなくなった場合のみ通話終了とみなし、通知を送信
            if len(voice_channel.members) == 0:
                logger.info(f"Source channel {voice_channel_before_id} ({guild_id}) is now empty. Considering call ended.")
                session = self.call_sessions[guild_id].pop(voice_channel_before_id) # 通話セッションを終了
                start_time = session["start_time"]
                call_duration = (now - start_time).total_seconds() # 通話時間を計算
                duration_str = format_duration(call_duration) # 表示用にフォーマット
                logger.debug(f"Call duration: {duration_str}")
                # 通話終了通知用のEmbedを作成
                embed = discord.Embed(title=constants.EMBED_TITLE_CALL_END, color=constants.EMBED_COLOR_CALL_END)
                embed.add_field(name="チャンネル", value=f"{voice_channel.name}")
                embed.add_field(name="通話時間", value=f"{duration_str}")
                # 通知チャンネルを取得し、通知を送信
                notification_channel_id = get_notification_channel_id(guild_id)
                if notification_channel_id:
                    notification_channel = self.bot.get_channel(notification_channel_id)
                    if notification_channel:
                        try:
                            await notification_channel.send(embed=embed)
                            logger.info(f"Sent call end notification to channel {notification_channel_id}.")
                        except discord.Forbidden:
                            logger.error(f"Error: Missing send permissions for channel {notification_channel.name} ({notification_channel_id}).")
                        except Exception as e:
                            logger.error(f"An error occurred while sending call end notification: {e}")
                    else:
                        # 通知チャンネルが見つからない場合のログ出力
                        logging.warning(f"Notification channel not found: Guild ID {guild_id}")
                else:
                    logger.info(f"Notification channel not set for guild {guild_id}. Call end notification will not be sent.")
            else:
                logger.debug(f"Source channel {voice_channel_before_id} ({guild_id}) still has members ({len(voice_channel.members)}). Not considering call ended.")
        else:
            logger.debug(f"No active call session in source channel {voice_channel_before_id} ({guild_id}).")


        # 移動先チャンネルへの入室処理 (通話通知用)
        voice_channel_after_id = channel_after.id
        if guild_id not in self.call_sessions:
            self.call_sessions[guild_id] = {}
            logger.debug(f"Created call_sessions entry for guild {guild_id}.")
        # 移動先のチャンネルに誰もいない状態から一人になった場合、または最初から一人で通話に参加した場合に通話開始とみなす
        if voice_channel_after_id not in self.call_sessions[guild_id] and len(channel_after.members) == 1:
             logger.info(f"Starting new call session in destination channel {voice_channel_after_id} ({guild_id}).")
             # 新しい通話セッションの開始時刻と最初のメンバーを記録
             start_time = now
             self.call_sessions[guild_id][voice_channel_after_id] = {"start_time": start_time, "first_member": member.id}
             # JSTに変換して表示用にフォーマット
             jst_time = convert_utc_to_jst(start_time)
             # 通話開始通知用のEmbedを作成
             embed = discord.Embed(title=constants.EMBED_TITLE_CALL_START, color=constants.EMBED_COLOR_CALL_START)
             embed.set_thumbnail(url=f"{member.avatar.url}?size=128")
             embed.add_field(name=constants.EMBED_FIELD_CHANNEL, value=f"{channel_after.name}")
             embed.add_field(name=constants.EMBED_FIELD_STARTED_BY, value=f"{member.display_name}")
             embed.add_field(name=constants.EMBED_FIELD_START_TIME, value=f"{jst_time.strftime('%Y/%m/%d %H:%M:%S')}")
             # 通知チャンネルを取得し、通知を送信
             notification_channel_id = get_notification_channel_id(guild_id)
             if notification_channel_id:
                 notification_channel = self.bot.get_channel(notification_channel_id)
                 if notification_channel:
                     try:
                         await notification_channel.send(content=constants.MENTION_EVERYONE, embed=embed, allowed_mentions=constants.ALLOWED_MENTIONS_EVERYONE)
                         logger.info(f"Sent call start notification to channel {notification_channel_id}.")
                     except discord.Forbidden:
                         logger.error(f"Error: Missing send permissions for channel {notification_channel.name} ({notification_channel_id}).")
                     except Exception as e:
                         logger.error(f"An error occurred while sending call start notification: {e}")
                 else:
                     # 通知チャンネルが見つからない場合のログ出力
                     logging.warning(f"Notification channel not found: Guild ID {guild_id}")
             else:
                 logger.info(f"Notification channel not set for guild {guild_id}. Call start notification will not be sent.")
        else:
            logger.debug(f"Channel {voice_channel_after_id} ({guild_id}) has an existing call session or member count is not 1. Skipping call start notification.")


        # 2人以上通話状態の記録（各メンバーごとに個別記録＋全参加者リストを維持する処理）
        # 移動元チャンネルからの退出処理と移動先チャンネルへの入室処理を統合して扱います。

        ended_sessions_from_before = [] # 移動元チャンネルで終了した個別のメンバーセッションデータを収集するリスト
        joined_session_data = None # 移動先チャンネルに参加したメンバーのデータ

        # 移動元チャンネルからの退出処理
        # 移動元チャンネルで2人以上通話セッションがアクティブな場合
        if key_before in self.active_voice_sessions:
            logger.debug(f"Active two-or-more-member call session in source channel {channel_before.id} ({guild_id}).")
            session_data_before = self.active_voice_sessions[key_before]
            # 移動したメンバーが現在セッションに在室中ならその個人分の退室処理を実施
            if member.id in session_data_before["current_members"]:
                logger.debug(f"Member {member.id} found in active_voice_sessions[{key_before}]['current_members']. Ending individual session.")
                join_time_leave = session_data_before["current_members"].pop(member.id) # メンバーを現在のメンバーリストから削除
                duration_leave = (now - join_time_leave).total_seconds() # そのメンバーの通話時間を計算
                ended_sessions_from_before.append((member.id, duration_leave, join_time_leave)) # 終了リストに追加
                logger.debug(f"Individual session end data for member {member.id}: Duration {duration_leave}, Join time {join_time_leave}")
            else:
                logger.debug(f"Member {member.id} was not found in active_voice_sessions[{key_before}]['current_members'].")


            # 移動元チャンネルの人数がconstants.MIN_MEMBERS_FOR_SESSION (現在2) 人未満になったら
            # そのチャンネルでの2人以上通話セッション全体を終了する処理を実施
            if len(channel_before.members) < constants.MIN_MEMBERS_FOR_SESSION:
                logger.info(f"Source channel {channel_before.id} ({guild_id}) member count dropped below {constants.MIN_MEMBERS_FOR_SESSION} ({len(channel_before.members)}). Ending two-or-more-member call session.")
                # セッション終了時の残メンバーの統計更新と通知チェック（voice_events.pyで処理）
                remaining_members_data = session_data_before["current_members"].copy()
                for m_id, join_time in remaining_members_data.items():
                    d = (now - join_time).total_seconds()
                    ended_sessions_from_before.append((m_id, d, join_time)) # 終了リストに追加
                    logger.debug(f"Data for member {m_id} remaining in ended session: Duration {d}, Join time {join_time}")
                    session_data_before["current_members"].pop(m_id) # 残メンバーを現在のメンバーリストから削除

                # セッション全体の通話時間を計算し、データベースに記録
                overall_duration = (now - session_data_before["session_start"]).total_seconds()
                logger.info(f"Recording overall two-or-more-member call session for channel {channel_before.id} ({guild_id}). Start time: {session_data_before['session_start']}, Duration: {overall_duration}, All participants: {list(session_data_before['all_participants'])}")
                await record_voice_session_to_db(session_data_before["session_start"], overall_duration, list(session_data_before["all_participants"]))
                self.active_voice_sessions.pop(key_before, None) # アクティブセッションから削除
                logger.debug(f"Removed channel {key_before} from active_voice_sessions.")

                # チャンネルの人数がconstants.MIN_MEMBERS_FOR_SESSION人未満になったら active_status_channels から削除
                self.active_status_channels.discard(key_before)
                logger.debug(f"Removed channel {key_before} from active_status_channels.")
                # 2人以上の通話がすべて終了した場合、ボットのステータス更新タスクを停止しステータスをクリア
                if not self.active_status_channels and self.update_call_status_task.is_running():
                    self.update_call_status_task.stop()
                    await self.bot.change_presence(activity=None)
                    logger.info("No active two-or-more-member call channels remaining, stopping status update task and clearing status.")
            else:
                logger.debug(f"Source channel {channel_before.id} ({guild_id}) still has {constants.MIN_MEMBERS_FOR_SESSION} or more members ({len(channel_before.members)}). Two-or-more-member call session continues.")

        else:
            logger.debug(f"No active two-or-more-member call session in source channel {channel_before.id} ({guild_id}).")


        # 移動先チャンネルへの入室処理
        # 移動先チャンネルの人数がconstants.MIN_MEMBERS_FOR_SESSION (現在2) 人以上の場合
        if len(channel_after.members) >= constants.MIN_MEMBERS_FOR_SESSION:
            logger.debug(f"Destination channel {channel_after.id} ({guild_id}) member count is {constants.MIN_MEMBERS_FOR_SESSION} or more ({len(channel_after.members)}).")
            if key_after not in self.active_voice_sessions:
                logger.info(f"Starting new two-or-more-member call session in destination channel {channel_after.id} ({guild_id}).")
                # 新しい2人以上通話セッションを開始
                self.active_voice_sessions[key_after] = {
                    "session_start": now,
                    "current_members": { m.id: now for m in channel_after.members }, # 現在のメンバーとその参加時刻を記録
                    "all_participants": set(m.id for m in channel_after.members) # 全参加者リストに現在のメンバーを追加
                }
                logger.debug(f"Created new active_voice_sessions entry: {key_after}")
            else:
                logger.debug(f"Destination channel {channel_after.id} ({guild_id}) has an existing two-or-more-member call session. Updating member list.")
                # 既存の2人以上通話セッションがある場合、新たに入室したメンバーを更新する
                session_data_after = self.active_voice_sessions[key_after]
                for m in channel_after.members:
                    if m.id not in session_data_after["current_members"]:
                        session_data_after["current_members"][m.id] = now # 新規参加メンバーの参加時刻を記録
                        logger.debug(f"Added member {m.id} to active_voice_sessions[{key_after}]['current_members'].")
                        if m.id == member.id: # 移動してきたメンバーの場合
                            # 移動してきたメンバーのデータとしてID、現在の通話時間（この時点では0）、参加時刻を記録
                            joined_session_data = (m.id, 0, now)
                            logger.debug(f"Recorded joined_session_data for moved member {m.id}.")
                    session_data_after["all_participants"].add(m.id) # 全参加者リストにメンバーを追加
                    logger.debug(f"Added member {m.id} to active_voice_sessions[{key_after}]['all_participants'].")


            # チャンネルの人数がconstants.MIN_MEMBERS_FOR_SESSION人以上になったら active_status_channels に追加
            if key_after not in self.active_status_channels:
                self.active_status_channels.add(key_after)
                logger.debug(f"Added channel {key_after} to active_status_channels.")
                # 初めて2人以上の通話が始まった場合、ボットのステータス更新タスクを開始
                if not self.update_call_status_task.is_running():
                    self.update_call_status_task.start()
                    logger.info("Started bot status update task.")
        else:
            logger.debug(f"Destination channel {channel_after.id} ({guild_id}) member count is less than {constants.MIN_MEMBERS_FOR_SESSION} ({len(channel_after.members)}).")
            # 移動先チャンネルの人数がconstants.MIN_MEMBERS_FOR_SESSION人未満の場合は、既にセッションが存在する場合のみ更新する
            # （一時的に人数が減ってもセッション自体は継続しているとみなす）
            if key_after in self.active_voice_sessions:
                logger.debug(f"Destination channel {channel_after.id} ({guild_id}) has an existing two-or-more-member call session. Updating member list.")
                session_data_after = self.active_voice_sessions[key_after]
                for m in channel_after.members:
                    if m.id not in session_data_after["current_members"]:
                        session_data_after["current_members"][m.id] = now # 新規参加メンバーの参加時刻を記録
                        logger.debug(f"Added member {m.id} to active_voice_sessions[{key_after}]['current_members'].")
                        if m.id == member.id: # 移動してきたメンバーの場合
                            # 移動してきたメンバーのデータとしてID、現在の通話時間（この時点では0）、参加時刻を記録
                            joined_session_data = (m.id, 0, now)
                            logger.debug(f"Recorded joined_session_data for moved member {m.id}.")
                    session_data_after["all_participants"].add(m.id) # 全参加者リストにメンバーを追加
                    logger.debug(f"Added member {m.id} to active_voice_sessions[{key_after}]['all_participants'].")
            else:
                logger.debug(f"No active two-or-more-member call session in destination channel {channel_after.id} ({guild_id}).")


        # 移動元チャンネルで終了した個別のメンバーセッションデータのリストと、
        # 移動先チャンネルに参加したメンバーのデータを返す
        return ended_sessions_from_before, joined_session_data


    # --- 二人以上の通話時間計算ヘルパー関数 ---
    def calculate_call_duration_seconds(self, start_time):
        """
        指定された開始時刻からの経過秒数を計算します。
        ボットのステータス更新タスクで使用されます。
        """
        now = datetime.datetime.now(datetime.timezone.utc)
        duration = (now - start_time).total_seconds()
        logger.debug(f"Elapsed time from start time {start_time}: {duration} seconds")
        return duration

    # アクティブな通話チャンネルとその通話時間を取得する
    def get_active_call_durations(self, guild_id: int):
        """
        指定されたギルドのアクティブな2人以上通話チャンネルとその通話時間を取得し、
        表示用にフォーマットして返します。/call_duration コマンドで使用されます。
        """
        logger.info(f"Fetching active call durations for guild {guild_id}.")
        active_calls = []
        now = datetime.datetime.now(datetime.timezone.utc)
        # アクティブな2人以上通話セッションを全て確認
        for key, session_data in self.active_voice_sessions.items():
            # 指定されたギルドのセッションのみを対象とする
            if key[0] == guild_id:
                channel = self.bot.get_channel(key[1])
                # チャンネルが存在し、ボイスチャンネルであることを確認
                if channel and isinstance(channel, discord.VoiceChannel):
                    # セッション開始からの経過時間を計算
                    duration_seconds = (now - session_data["session_start"]).total_seconds()
                    # 表示用にフォーマット
                    formatted_duration = format_duration(duration_seconds)
                    # チャンネル名とフォーマット済み通話時間をリストに追加
                    active_calls.append({"channel_name": channel.name, "duration": formatted_duration})
                    logger.debug(f"Active call channel: {channel.name}, Duration: {formatted_duration}")
                else:
                    logger.warning(f"Active session channel {key[1]} ({key[0]}) not found or is not a voice channel.")
        logger.info(f"Number of active call channels for guild {guild_id}: {len(active_calls)}")
        # アクティブな通話チャンネルとその通話時間のリストを返す
        return active_calls

    # --- ステータス通話時間更新タスク ---
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
            if guild and channel and channel_key_to_display in self.active_voice_sessions:
                logger.debug(f"Channel to display in status: {channel.name} ({guild.name})")
                # 選択したチャンネルの通話時間を計算
                session_data = self.active_voice_sessions[channel_key_to_display]
                duration_seconds = self.calculate_call_duration_seconds(session_data["session_start"])
                # 表示用にフォーマット
                formatted_duration = format_duration(duration_seconds)
                # ボットのカスタムステータスを設定
                activity = discord.CustomActivity(name=f"{channel.name}: {formatted_duration}")
                await self.bot.change_presence(activity=activity)
                logger.info(f"Updated bot status: {activity.name}")
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

    async def record_session_end(self, guild_id: int, channel_id: int):
        """
        特定のチャンネルでの2人以上通話セッションが終了した時の処理。
        セッション全体の通話時間をデータベースに記録し、アクティブセッションリストから削除します。
        終了した個別のメンバーセッションデータを返します（統計更新と通知チェックは voice_events.py で行います）。
        """
        logger.info(f"Starting two-or-more-member call session end process for channel {channel_id} ({guild_id}).")
        key = (guild_id, channel_id)
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
            logger.info(f"Recording overall two-or-more-member call session for channel {channel_id} ({guild_id}). Start time: {session_data['session_start']}, Duration: {overall_duration}, All participants: {list(session_data['all_participants'])}")
            await record_voice_session_to_db(session_data["session_start"], overall_duration, list(session_data["all_participants"]))
            self.active_voice_sessions.pop(key, None) # アクティブセッションから削除
            logger.debug(f"Removed channel {key} from active_voice_sessions.")

            # チャンネルの人数が1人以下になったら active_status_channels から削除
            self.active_status_channels.discard(key)
            logger.debug(f"Removed channel {key} from active_status_channels.")
            # 2人以上の通話がすべて終了した場合、ボットのステータス更新タスクを停止しステータスをクリア
            if not self.active_status_channels and self.update_call_status_task.is_running():
                self.update_call_status_task.stop()
                await self.bot.change_presence(activity=None)
                logger.info("No active two-or-more-member call channels remaining, stopping status update task and clearing status.")
        else:
            logger.warning(f"No active two-or-more-member call session found for channel {channel_id} ({guild_id}). Skipping end process.")

        return ended_sessions_data # 終了した個別のメンバーセッションデータのリストを返す


    def start_session(self, guild_id: int, channel_id: int, members: list[discord.Member]):
        """
        新しい2人以上通話セッションを開始します。
        voice_events.py でチャンネルにconstants.MIN_MEMBERS_FOR_SESSION人以上になった時に呼び出されます。
        """
        logger.info(f"Starting new two-or-more-member call session in channel {channel_id} ({guild_id}).")
        key = (guild_id, channel_id)
        now = datetime.datetime.now(datetime.timezone.utc)
        # 新しいセッションデータを初期化
        self.active_voice_sessions[key] = {
            "session_start": now, # セッション開始時刻
            "current_members": { m.id: now for m in members }, # 現在のメンバーとその参加時刻
            "all_participants": set(m.id for m in members) # 全参加者リスト
        }
        logger.debug(f"Created new active_voice_sessions entry: {key}")
        # active_status_channels にチャンネルを追加
        self.active_status_channels.add(key)
        logger.debug(f"Added channel {key} to active_status_channels.")
        # ステータス更新タスクが実行中でなければ開始
        if not self.update_call_status_task.is_running():
            self.update_call_status_task.start()
            logger.info("Started bot status update task.")

    def update_session_members(self, guild_id: int, channel_id: int, members: list[discord.Member]):
        """
        既存の2人以上通話セッションのメンバーリストを更新します。
        チャンネルにメンバーが追加された際に呼び出され、新規参加メンバーをセッションに追加します。
        """
        logger.info(f"Updating member list for two-or-more-member call session in channel {channel_id} ({guild_id}).")
        key = (guild_id, channel_id)
        now = datetime.datetime.now(datetime.timezone.utc)
        # 指定されたチャンネルの2人以上通話セッションがアクティブな場合
        if key in self.active_voice_sessions:
            logger.debug(f"Active session found for channel {key}.")
            session_data = self.active_voice_sessions[key]
            # チャンネルにいる各メンバーを確認
            for m in members:
                # まだセッションのcurrent_membersリストにいないメンバーであれば追加
                if m.id not in session_data["current_members"]:
                    session_data["current_members"][m.id] = now # 参加時刻を記録
                    logger.debug(f"Added member {m.id} to active_voice_sessions[{key}]['current_members'].")
                session_data["all_participants"].add(m.id) # 全参加者リストに追加
                logger.debug(f"Added member {m.id} to active_voice_sessions[{key}]['all_participants'].")
            logger.debug(f"Member list update complete for channel {key}. Current members: {list(session_data['current_members'].keys())}, All participants: {list(session_data['all_participants'])}")
        else:
            logger.warning(f"No active two-or-more-member call session found for channel {channel_id} ({guild_id}). Skipping member list update.")
