import discord
import datetime
import asyncio
import logging
from discord.ext import commands # Cog を使用するためにインポート

from database import get_total_call_time, get_guild_settings, update_member_monthly_stats, record_voice_session_to_db
import config
import voice_state_manager
import formatters
import constants

# ロガーを取得
logger = logging.getLogger(__name__)

class SleepCheckManager:
    def __init__(self, bot):
        self.bot = bot
        logger.info("SleepCheckManager initialized.")
        # 一人以下の状態になった通話チャンネルとその時刻、メンバー、関連タスクを記録する辞書
        # キー: (guild_id, voice_channel_id), 値: {"start_time": datetimeオブジェクト, "member_id": int, "task": asyncio.Task}
        self.lonely_voice_channels = {}

        # 寝落ち確認メッセージとそれに対するリアクション監視タスクを記録する辞書
        # キー: message_id, 値: {"member_id": int, "task": asyncio.Task}
        self.sleep_check_messages = {}

        # ボットがサーバーミュートしたメンバーのIDを記録するリスト
        self.bot_muted_members = []

    # bot_muted_members にメンバーを追加するヘルパー関数
    def add_bot_muted_member(self, member_id: int):
        if member_id not in self.bot_muted_members:
            self.bot_muted_members.append(member_id)
            logger.info(f"Added member {member_id} to bot_muted_members.")

    # bot_muted_members からメンバーを削除するヘルパー関数
    def remove_bot_muted_member(self, member_id: int):
        if member_id in self.bot_muted_members:
            self.bot_muted_members.remove(member_id)
            logger.info(f"Removed member {member_id} from bot_muted_members.")

    # lonely_voice_channels にチャンネルを追加するヘルパー関数
    def add_lonely_channel(self, guild_id: int, channel_id: int, member_id: int, task: asyncio.Task):
        key = (guild_id, channel_id)
        self.lonely_voice_channels[key] = {
            "start_time": datetime.datetime.now(datetime.timezone.utc),
            "member_id": member_id,
            "task": task
        }
        logger.info(f"Channel {channel_id} ({guild_id}) has one or fewer members. Member: {member_id}")

    # lonely_voice_channels からチャンネルを削除するヘルパー関数
    def remove_lonely_channel(self, guild_id: int, channel_id: int, cancel_task: bool = True):
        key = (guild_id, channel_id)
        if key in self.lonely_voice_channels:
            if cancel_task and self.lonely_voice_channels[key]["task"] and not self.lonely_voice_channels[key]["task"].cancelled():
                self.lonely_voice_channels[key]["task"].cancel()
                logger.debug(f"Cancelled lonely state task for channel {channel_id} ({guild_id}).")
            self.lonely_voice_channels.pop(key)
            logger.info(f"Removed lonely state for channel {channel_id} ({guild_id}).")

    # --- 寝落ち確認とミュート処理 ---
    async def check_lonely_channel(self, guild_id: int, channel_id: int, member_id: int, notification_channel_id: int | None):
        logger.info(f"Starting lonely state check for channel {channel_id} ({guild_id}). Member: {member_id}")
        timeout_seconds = await self._get_lonely_timeout_seconds(guild_id)
        logger.debug(f"Configured timeout: {timeout_seconds} seconds")
        await asyncio.sleep(timeout_seconds) # 設定された時間待機

        # 再度チャンネルの状態を確認
        guild = self.bot.get_guild(guild_id)
        if not guild:
            logger.warning(f"Guild {guild_id} not found. Ending lonely state check.")
            return
        channel = guild.get_channel(channel_id)
        # チャンネルが存在しない、またはタイムアウトしたメンバーがチャンネルにいない場合は処理しない
        if not channel or member_id not in [m.id for m in channel.members]:
            logger.info(f"Channel {channel_id} does not exist or member {member_id} is not in the channel. Ending lonely state check.")
            self.remove_lonely_channel(guild_id, channel_id, cancel_task=False) # タスク自体は完了しているのでキャンセルは不要
            return

        # チャンネルに一人だけ残っている、または複数人だが最初に一人になったメンバーがまだいる場合
        logger.info(f"Channel {channel_id} ({guild_id}) remains in a lonely state. Sending sleep check message.")
        # 寝落ち確認メッセージを送信
        if notification_channel_id:
            notification_channel = self.bot.get_channel(notification_channel_id)
            if notification_channel:
                lonely_member = guild.get_member(member_id)
                if lonely_member:
                    embed = discord.Embed(
                        title=constants.EMBED_TITLE_SLEEP_CHECK,
                        description=f"{lonely_member.mention}{constants.EMBED_DESCRIPTION_SLEEP_CHECK.format(channel_name=channel.name)}",
                        color=constants.EMBED_COLOR_WARNING
                    )
                    try:
                        message = await notification_channel.send(embed=embed)
                        await message.add_reaction(constants.REACTION_EMOJI_SLEEP_CHECK) # :white_check_mark: 絵文字を追加
                        logger.info(f"Sent sleep check message to channel {notification_channel_id}. Message ID: {message.id}")

                        # リアクション監視タスクを開始
                        reaction_task = asyncio.create_task(self.wait_for_reaction(message.id, member_id, guild_id, channel_id, notification_channel_id))
                        self.sleep_check_messages[message.id] = {"member_id": member_id, "task": reaction_task}
                        logger.debug(f"Started reaction monitoring task. Message ID: {message.id}")

                    except discord.Forbidden:
                        logger.error(f"Error: No permission to send messages to channel {notification_channel.name} ({notification_channel_id}).")
                    except Exception as e:
                        logger.error(f"An error occurred while sending sleep check message: {e}")
                else:
                     logger.warning(f"Member {member_id} not found. Removing from lonely state management.")
                     # メンバーが見つからない場合も状態管理から削除
                     key = (guild_id, channel_id)
                     if key in self.lonely_voice_channels:
                        self.lonely_voice_channels.pop(key)
            else:
                logger.warning(f"Notification channel not found: Guild ID {guild_id}. Removing from lonely state management.")
                # 通知チャンネルがない場合も状態管理から削除
                key = (guild_id, channel_id)
                if key in self.lonely_voice_channels:
                    self.lonely_voice_channels.pop(key)
        else:
            logger.warning(f"Notification channel not set for guild {guild.name} ({guild_id}). Cannot send sleep check message. Removing from lonely state management.")
            # 通知チャンネルが設定されていない場合も状態管理から削除
            key = (guild_id, channel_id)
            if key in self.lonely_voice_channels:
                self.lonely_voice_channels.pop(key)


    async def wait_for_reaction(self, message_id: int, member_id: int, guild_id: int, channel_id: int, notification_channel_id: int | None):
        logger.info(f"Starting reaction monitoring for message {message_id}. Member: {member_id}")
        settings = await get_guild_settings(guild_id)
        wait_seconds = settings["reaction_wait_minutes"] * constants.SECONDS_PER_MINUTE
        logger.debug(f"Configured reaction wait time: {wait_seconds} seconds")

        try:
            # 指定された絵文字、ユーザーからのリアクションを待つ
            def check(reaction, user):
                return user.id == member_id and str(reaction.emoji) == constants.REACTION_EMOJI_SLEEP_CHECK and reaction.message.id == message_id

            await self.bot.wait_for('reaction_add', timeout=wait_seconds, check=check)
            logger.info(f"Member {member_id} reacted to message {message_id}. Cancelling mute process.")
            guild = self.bot.get_guild(guild_id)
            if guild and notification_channel_id:
                notification_channel = self.bot.get_channel(notification_channel_id)
                if notification_channel:
                    try:
                        member = guild.get_member(member_id)
                        if member:
                            embed = discord.Embed(title=constants.EMBED_TITLE_SLEEP_CHECK, description=f"{member.mention}{constants.EMBED_DESCRIPTION_SLEEP_CHECK_CANCEL}", color=constants.EMBED_COLOR_SUCCESS)
                            await notification_channel.send(embed=embed)
                            logger.info(f"Sent mute cancellation message to channel {notification_channel.id}.")
                    except discord.Forbidden:
                        logger.error(f"Error: No permission to send messages to channel {notification_channel.name} ({notification_channel.id}).")
                    except Exception as e:
                        logger.error(f"An error occurred while sending mute cancellation message: {e}")


        except asyncio.TimeoutError:
            # タイムアウトした場合、ミュート処理を実行
            logger.info(f"No reaction to message {message_id}. Muting member {member_id}.")
            guild = self.bot.get_guild(guild_id)
            if guild:
                member = guild.get_member(member_id)
                if member:
                    try:
                        await member.edit(mute=True, deafen=True)
                        logger.info(f"Muted member {member.display_name} ({member.id}).")
                        # ボットがミュートしたメンバーを記録
                        self.add_bot_muted_member(member.id)

                        if notification_channel_id:
                            notification_channel = self.bot.get_channel(notification_channel_id)
                            if notification_channel:
                                try:
                                    embed = discord.Embed(title=constants.EMBED_TITLE_SLEEP_CHECK, description=f"{member.mention}{constants.EMBED_DESCRIPTION_SLEEP_CHECK_MUTE}", color=constants.EMBED_COLOR_ERROR)
                                    await notification_channel.send(embed=embed)
                                    logger.info(f"Sent mute execution message to channel {notification_channel.id}.")
                                except discord.Forbidden:
                                    logger.error(f"Error: No permission to send messages to channel {notification_channel.name} ({notification_channel.id}).")
                                except Exception as e:
                                    logger.error(f"An error occurred while sending mute execution message: {e}")

                    except discord.Forbidden:
                        logger.error(f"Error: No permission to unmute member {member.display_name} ({member.id}).")
                    except Exception as e:
                        logger.error(f"An error occurred while unmuting member: {e}")
                else:
                    logger.warning(f"Member {member_id} not found.")
            else:
                logger.warning(f"Guild {guild_id} not found.")

        finally:
            # 処理が完了したら、一時的な記録から削除
            if message_id in self.sleep_check_messages:
                self.sleep_check_messages.pop(message_id)
                logger.debug(f"Removed message {message_id} from sleep_check_messages.")
            # チャンネルの状態管理からも削除（ミュートされたか反応があったかで一人以下の状態は終了とみなす）
            key = (guild_id, channel_id)
            if key in self.lonely_voice_channels:
                 self.lonely_voice_channels.pop(key)
                 logger.debug(f"Removed channel {channel_id} ({guild_id}) from lonely_voice_channels.")

    # get_lonely_timeout_seconds を SleepCheckManager のメソッドとして移動
    async def _get_lonely_timeout_seconds(self, guild_id):
        settings = await get_guild_settings(guild_id)
        return settings[constants.COLUMN_LONELY_TIMEOUT_MINUTES] * constants.SECONDS_PER_MINUTE # 分を秒に変換


# --- イベントハンドラ ---

class VoiceEvents(commands.Cog):
    def __init__(self, bot, sleep_check_manager: SleepCheckManager, voice_state_manager: voice_state_manager.VoiceStateManager):
        self.bot = bot
        self.sleep_check_manager = sleep_check_manager
        self.voice_state_manager = voice_state_manager
        logger.info("VoiceEvents Cog initialized.")

    # --- 10時間達成通知用ヘルパー関数 ---
    async def _check_and_notify_milestone(self, member: discord.Member, guild: discord.Guild, before_total: float, after_total: float, notification_channel_id: int | None):
        logger.info(f"Checking for milestone notification. Member: {member.id}, Guild: {guild.id}, Before: {before_total}, After: {after_total}")
        guild_id = str(guild.id)

        if notification_channel_id is None:
            logger.debug(f"Notification channel not set for guild {guild_id}. Skipping milestone notification.")
            return # 通知先チャンネルが設定されていない場合は何もしない

        notification_channel = self.bot.get_channel(notification_channel_id)
        if not notification_channel:
            logger.warning(f"Notification channel not found: Guild ID {guild_id}, Channel ID {notification_channel_id}")
            return

        hour_threshold = constants.MILESTONE_THRESHOLD_SECONDS
        before_milestone = int(before_total // hour_threshold)
        after_milestone = int(after_total // hour_threshold)
        logger.debug(f"Before milestone: {before_milestone}, After milestone: {after_milestone}")

        if after_milestone > before_milestone:
            achieved_hours = after_milestone * 10 # 10時間ごとのマイルストーンなので 10 を乗算
            logger.info(f"Member {member.id} achieved {achieved_hours} hour milestone.")
            embed = discord.Embed(
                title=constants.EMBED_TITLE_MILESTONE,
                description=constants.EMBED_DESCRIPTION_MILESTONE.format(member=member, achieved_hours=achieved_hours),
                color=constants.EMBED_COLOR_MILESTONE
            )
            embed.set_thumbnail(url=member.display_avatar.url)
            embed.add_field(name=constants.EMBED_FIELD_MEMBER, value=member.display_name, inline=True)
            embed.add_field(name=constants.EMBED_FIELD_ACHIEVED_TIME, value=f"{achieved_hours} 時間", inline=True)
            embed.add_field(name=constants.EMBED_FIELD_CURRENT_TOTAL, value=formatters.format_duration(after_total), inline=False) # Use imported formatters
            embed.timestamp = datetime.datetime.now(constants.TIMEZONE_JST)

            try:
                await notification_channel.send(embed=embed)
                logger.info(f"Sent milestone notification to channel {notification_channel_id}.")
            except discord.Forbidden:
                logger.error(f"Error: No permission to send messages to channel {notification_channel.name} ({notification_channel_id}).")
            except Exception as e:
                logger.error(f"An error occurred while sending notification: {e}")
        else:
            logger.debug("No milestone achieved.")

    # チャンネルに入室した場合の処理
    async def _handle_join(self, member, channel_after):
        logger.info(f"Member {member.id} joined channel {channel_after.id} ({channel_after.name}).")
        guild_id = member.guild.id
        key_after = (guild_id, channel_after.id)

        # 入室したチャンネルが一人以下になった場合、そのメンバーに対して一人以下の状態を開始
        if len(channel_after.members) == 1:
            lonely_member = channel_after.members[0]
            if key_after not in self.sleep_check_manager.lonely_voice_channels and lonely_member.id not in self.sleep_check_manager.bot_muted_members:
                logger.debug(f"Channel {channel_after.id} ({guild_id}) has one or fewer members. Starting sleep check. Member: {lonely_member.id}")
                notification_channel_id = config.get_notification_channel_id(guild_id) # config から取得
                task = asyncio.create_task(self.sleep_check_manager.check_lonely_channel(guild_id, channel_after.id, lonely_member.id, notification_channel_id))
                self.sleep_check_manager.add_lonely_channel(guild_id, channel_after.id, lonely_member.id, task)
        # 入室したチャンネルが複数人になった場合、一人以下の状態を解除
        elif len(channel_after.members) > 1:
            if key_after in self.sleep_check_manager.lonely_voice_channels:
                logger.debug(f"Multiple members joined channel {channel_after.id} ({guild_id}). Removing lonely state.")
                self.sleep_check_manager.remove_lonely_channel(guild_id, channel_after.id)

        # VoiceStateManager に処理を委譲
        await self.voice_state_manager.notify_member_joined(member, channel_after)
        logger.debug(f"VoiceStateManager.notify_member_joined processing complete. Member: {member.id}, Channel: {channel_after.id}")

        # ボットによってミュートされたメンバーが再入室した場合、ミュートを解除
        if member.id in self.sleep_check_manager.bot_muted_members:
            logger.info(f"Bot-muted member {member.id} rejoined. Scheduling unmute.")
            async def unmute_after_delay(m: discord.Member):
                logger.debug(f"Starting delayed unmute process for member {m.id}.")
                # チャンネルの状態変化が完全に反映されるのを待つため、少し遅延させる
                await asyncio.sleep(constants.UNMUTE_DELAY_SECONDS)
                try:
                    await m.edit(mute=False, deafen=False)
                    self.sleep_check_manager.remove_bot_muted_member(m.id)
                    logger.info(f"Unmuted member {m.display_name} ({m.id}) due to rejoining.")

                    notification_channel_id = config.get_notification_channel_id(m.guild.id)
                    if notification_channel_id:
                        notification_channel = self.bot.get_channel(notification_channel_id)
                        if notification_channel:
                            try:
                                embed = discord.Embed(title=constants.EMBED_TITLE_SLEEP_CHECK, description=f"{m.mention}{constants.EMBED_DESCRIPTION_UNMUTE_ON_REJOIN}", color=constants.EMBED_COLOR_SUCCESS)
                                await notification_channel.send(embed=embed)
                                logger.info(f"Sent unmute on rejoin message to channel {notification_channel.id}.")
                            except discord.Forbidden:
                                logger.error(f"Error: No permission to send messages to channel {notification_channel.name} ({notification_channel.id}).")
                            except Exception as e:
                                logger.error(f"An error occurred while sending unmute on rejoin message: {e}")

                except discord.Forbidden:
                    logger.error(f"Error: No permission to unmute member {m.display_name} ({m.id}).")
                except Exception as e:
                    logger.error(f"An error occurred while unmuting member: {e}")
                logger.debug(f"Delayed unmute process for member {m.id} completed.")

            asyncio.create_task(unmute_after_delay(member))

    # チャンネルから退出した場合の処理
    async def _handle_leave(self, member, channel_before):
        logger.info(f"Member {member.id} left channel {channel_before.id} ({channel_before.name}).")
        guild_id = member.guild.id
        key_before = (guild_id, channel_before.id)

        # 退室したメンバーに関連付けられた寝落ち確認タスクがあればキャンセル
        message_ids_to_remove = []
        for message_id, data in self.sleep_check_manager.sleep_check_messages.items():
            if data["member_id"] == member.id:
                if data["task"] and not data["task"].cancelled():
                    data["task"].cancel()
                    logger.info(f"Cancelled reaction monitoring task for message {message_id} due to member {member.id} leaving.")
                message_ids_to_remove.append(message_id)

        for message_id in message_ids_to_remove:
            self.sleep_check_manager.sleep_check_messages.pop(message_id)
            logger.debug(f"Removed message {message_id} from sleep_check_messages.")

        # 退室したチャンネルに誰もいなくなった場合、一人以下の状態を解除
        if channel_before is not None and len(channel_before.members) == 0:
            if key_before in self.sleep_check_manager.lonely_voice_channels:
                logger.debug(f"Channel {channel_before.id} ({guild_id}) is empty. Removing lonely state.")
                self.sleep_check_manager.remove_lonely_channel(guild_id, channel_before.id)
        # 退室したチャンネルに一人だけ残った場合、そのメンバーに対して一人以下の状態を開始
        elif channel_before is not None and len(channel_before.members) == 1:
            lonely_member = channel_before.members[0]
            if key_before not in self.sleep_check_manager.lonely_voice_channels and lonely_member.id not in self.sleep_check_manager.bot_muted_members:
                logger.debug(f"Only one member left in channel {channel_before.id} ({guild_id}). Starting sleep check. Member: {lonely_member.id}")
                notification_channel_id = config.get_notification_channel_id(guild_id) # config から取得
                task = asyncio.create_task(self.sleep_check_manager.check_lonely_channel(guild_id, channel_before.id, lonely_member.id, notification_channel_id))
                self.sleep_check_manager.add_lonely_channel(guild_id, channel_before.id, lonely_member.id, task)

        # VoiceStateManager に処理を委譲し、統計更新が必要なデータを取得
        ended_sessions_data = await self.voice_state_manager.notify_member_left(member, channel_before)
        logger.debug(f"VoiceStateManager.notify_member_left processing complete. Member: {member.id}, Channel: {channel_before.id}. Ended sessions count: {len(ended_sessions_data)}")
        # VoiceStateManager から統計更新が必要なデータが返された場合、処理関数に委譲
        if ended_sessions_data:
            await self._process_session_end_data(member.guild, ended_sessions_data)

    async def _process_session_end_data(self, guild: discord.Guild, ended_sessions_data: list):
        """
        VoiceStateManagerから返された終了した個別のメンバーセッションデータを処理し、
        統計更新とマイルストーン通知を行います。
        """
        logger.info(f"Starting processing of ended session data for guild {guild.id}. Data count: {len(ended_sessions_data)}")
        for member_id, duration, join_time in ended_sessions_data:
            logger.debug(f"Processing session end data for member {member_id}. Duration: {duration}, Join time: {join_time}")
            before_total = await get_total_call_time(member_id)
            month_key = join_time.strftime("%Y-%m")
            await update_member_monthly_stats(month_key, member_id, duration)
            after_total = await get_total_call_time(member_id)
            logger.debug(f"Updated monthly stats for member {member_id}. Before Total: {before_total}, After Total: {after_total}")
            m_obj = guild.get_member(member_id)
            if m_obj:
                notification_channel_id = config.get_notification_channel_id(guild.id) # config から取得
                await self._check_and_notify_milestone(m_obj, guild, before_total, after_total, notification_channel_id)
            else:
                logger.warning(f"Member {member_id} not found in guild {guild.id}. Cannot check/notify milestone.")
        logger.info(f"Finished processing of ended session data for guild {guild.id}.")


    # チャンネル間を移動した場合の処理
    async def _handle_move(self, member, channel_before, channel_after):
        logger.info(f"Member {member.id} moved from channel {channel_before.id} ({channel_before.name}) to channel {channel_after.id} ({channel_after.name}).")
        guild_id = member.guild.id
        key_before = (guild_id, channel_before.id)
        key_after = (guild_id, channel_after.id)

        # 移動元チャンネルに誰もいなくなった場合、一人以下の状態を解除
        if len(channel_before.members) == 0:
            if key_before in self.sleep_check_manager.lonely_voice_channels:
                logger.debug(f"Source channel {channel_before.id} ({guild_id}) is empty. Removing lonely state.")
                self.sleep_check_manager.remove_lonely_channel(guild_id, channel_before.id)
        # 移動元チャンネルに一人だけ残った場合、そのメンバーに対して一人以下の状態を開始
        elif len(channel_before.members) == 1:
            lonely_member = channel_before.members[0]
            if key_before not in self.sleep_check_manager.lonely_voice_channels and lonely_member.id not in self.sleep_check_manager.bot_muted_members:
                logger.debug(f"Only one member left in source channel {channel_before.id} ({guild_id}). Starting sleep check. Member: {lonely_member.id}")
                task = asyncio.create_task(self.sleep_check_manager.check_lonely_channel(guild_id, channel_before.id, lonely_member.id))
                self.sleep_check_manager.add_lonely_channel(guild_id, channel_before.id, lonely_member.id, task)

        # 移動先チャンネルが一人以下になった場合、そのメンバーに対して一人以下の状態を開始
        if len(channel_after.members) == 1:
            lonely_member = channel_after.members[0]
            if key_after not in self.sleep_check_manager.lonely_voice_channels and lonely_member.id not in self.sleep_check_manager.bot_muted_members:
                logger.debug(f"Destination channel {channel_after.id} ({guild_id}) has one or fewer members. Starting sleep check. Member: {lonely_member.id}")
                notification_channel_id = config.get_notification_channel_id(guild_id) # config から取得
                task = asyncio.create_task(self.sleep_check_manager.check_lonely_channel(guild_id, channel_after.id, lonely_member.id, notification_channel_id))
                self.sleep_check_manager.add_lonely_channel(guild_id, channel_after.id, lonely_member.id, task)
        # 移動先チャンネルが複数人になった場合、一人以下の状態を解除
        elif len(channel_after.members) > 1:
            if key_after in self.sleep_check_manager.lonely_voice_channels:
                logger.debug(f"Multiple members joined destination channel {channel_after.id} ({guild_id}). Removing lonely state.")
                self.sleep_check_manager.remove_lonely_channel(guild_id, channel_after.id)

        # VoiceStateManager に処理を委譲し、統計更新が必要なデータを取得
        ended_sessions_from_before, joined_session_data = await self.voice_state_manager.notify_member_moved(member, channel_before, channel_after)
        logger.debug(f"VoiceStateManager.notify_member_moved processing complete. Member: {member.id}, Source: {channel_before.id}, Destination: {channel_after.id}. Ended sessions count: {len(ended_sessions_from_before)}, Joined session data: {joined_session_data is not None}")

        # 移動元での退出による統計更新とマイルストーン通知
        if ended_sessions_from_before:
            await self._process_session_end_data(member.guild, ended_sessions_from_before)

        # 移動先での入室による統計更新とマイルストーン通知 (移動してきたメンバー自身の場合のみ)
        # 移動直後は通話時間0として記録（新しいセッションの開始）
        if joined_session_data is not None:
             member_id_join, duration_join, join_time_join = joined_session_data
             logger.debug(f"Starting stats update process due to joining destination channel. Member: {member_id_join}, Duration: {duration_join}, Join time: {join_time_join}")
             # 移動直後は通話時間0として記録（新しいセッションの開始）
             # _process_session_end_data と同様のロジックを適用しつつ、duration を 0 とする
             before_total_join = await get_total_call_time(member_id_join)
             month_key_join = join_time_join.strftime("%Y-%m")
             await update_member_monthly_stats(month_key_join, member_id_join, 0) # duration は 0
             after_total_join = await get_total_call_time(member_id_join)
             logger.debug(f"Updated monthly stats for member {member_id_join}. Before Total: {before_total_join}, After Total: {after_total_join}")
             m_obj_join = member.guild.get_member(member_id_join) if member.guild else None
             if m_obj_join:
                 notification_channel_id = config.get_notification_channel_id(member.guild.id) # config から取得
                 await self._check_and_notify_milestone(m_obj_join, member.guild, before_total_join, after_total_join, notification_channel_id)


        # ボットによってミュートされたメンバーがチャンネル移動した場合、ミュートを解除
        if member.id in self.sleep_check_manager.bot_muted_members:
            logger.info(f"Bot-muted member {member.id} moved channels. Scheduling unmute.")
            async def unmute_after_delay(m: discord.Member):
                logger.debug(f"Starting delayed unmute process for member {m.id}.")
                await asyncio.sleep(constants.UNMUTE_DELAY_SECONDS) # 1秒待機
                try:
                    await m.edit(mute=False, deafen=False)
                    self.sleep_check_manager.remove_bot_muted_member(m.id)
                    logger.info(f"Unmuted member {m.display_name} ({m.id}) due to channel move.")

                    notification_channel_id = config.get_notification_channel_id(m.guild.id)
                    if notification_channel_id:
                        notification_channel = self.bot.get_channel(notification_channel_id)
                        if notification_channel:
                            try:
                                embed = discord.Embed(title=constants.EMBED_TITLE_SLEEP_CHECK, description=f"{m.mention}{constants.EMBED_DESCRIPTION_UNMUTE_ON_REJOIN}", color=constants.EMBED_COLOR_SUCCESS)
                                await notification_channel.send(embed=embed)
                                logger.info(f"Sent unmute on channel move message to channel {notification_channel.id}.")
                            except discord.Forbidden:
                                logger.error(f"Error: No permission to send messages to channel {notification_channel.name} ({notification_channel.id}).")
                            except Exception as e:
                                logger.error(f"An error occurred while sending unmute on channel move message: {e}")

                except discord.Forbidden:
                    logger.error(f"Error: No permission to unmute member {m.display_name} ({m.id}).")
                except Exception as e:
                    logger.error(f"An error occurred while unmuting member: {e}")
                logger.debug(f"Delayed unmute process for member {m.id} completed.")

            asyncio.create_task(unmute_after_delay(member))

    # 同一チャンネル内での状態変化（ミュート、デフなど）の処理
    async def _handle_state_change(self, member, before, after):
        logger.debug(f"Detected state change for member {member.id} within the same channel. Channel: {before.channel.id}")
        # このメソッドは、同一チャンネル内でのミュート、デフ、ストリーム開始/終了などの状態変化を処理するために存在します。
        # 現在の要件ではこれらの状態変化に対して特別なアクションは必要ないため、passしています。
        # 将来的にこれらの状態変化に対応する機能を追加する場合に、このメソッド内にロジックを記述します。
        pass

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        logger.info(f"on_voice_state_update event occurred: Member {member.id}, Before: {before.channel}, After: {after.channel}")
        channel_before = before.channel
        channel_after = after.channel

        if channel_before is None and channel_after is not None:
            # チャンネルに入室した場合
            await self._handle_join(member, channel_after)
        elif channel_before is not None and channel_after is None:
            # チャンネルから退出した場合
            await self._handle_leave(member, channel_before)
        elif channel_before is not None and channel_after is not None and channel_before != channel_after:
            # チャンネル間を移動した場合
            await self._handle_move(member, channel_before, channel_after)
        elif channel_before is not None and channel_after is not None and channel_before == channel_after:
            # 同一チャンネル内での状態変化
            await self._handle_state_change(member, before, after)

        pass
