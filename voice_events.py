import discord
import datetime
import asyncio
import logging # logging モジュールをインポート
from discord.ext import commands # Cog を使用するためにインポート

from database import get_total_call_time, get_guild_settings, update_member_monthly_stats, record_voice_session_to_db
import config # config モジュールをインポート
import voice_state_manager # voice_state_manager モジュールをインポート
import formatters # formatters モジュールをインポート
import constants # constants モジュールをインポート

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
            logger.info(f"メンバー {member_id} をbot_muted_membersに追加しました。")

    # bot_muted_members からメンバーを削除するヘルパー関数
    def remove_bot_muted_member(self, member_id: int):
        if member_id in self.bot_muted_members:
            self.bot_muted_members.remove(member_id)
            logger.info(f"メンバー {member_id} をbot_muted_membersから削除しました。")

    # lonely_voice_channels にチャンネルを追加するヘルパー関数
    def add_lonely_channel(self, guild_id: int, channel_id: int, member_id: int, task: asyncio.Task):
        key = (guild_id, channel_id)
        self.lonely_voice_channels[key] = {
            "start_time": datetime.datetime.now(datetime.timezone.utc),
            "member_id": member_id,
            "task": task
        }
        logger.info(f"チャンネル {channel_id} ({guild_id}) が一人以下になりました。メンバー: {member_id}")

    # lonely_voice_channels からチャンネルを削除するヘルパー関数
    def remove_lonely_channel(self, guild_id: int, channel_id: int, cancel_task: bool = True):
        key = (guild_id, channel_id)
        if key in self.lonely_voice_channels:
            if cancel_task and self.lonely_voice_channels[key]["task"] and not self.lonely_voice_channels[key]["task"].cancelled():
                self.lonely_voice_channels[key]["task"].cancel()
                logger.debug(f"チャンネル {channel_id} ({guild_id}) の一人以下の状態タスクをキャンセルしました。")
            self.lonely_voice_channels.pop(key)
            logger.info(f"チャンネル {channel_id} ({guild_id}) の一人以下の状態を解除しました。")

    # --- 寝落ち確認とミュート処理 ---
    async def check_lonely_channel(self, guild_id: int, channel_id: int, member_id: int):
        logger.info(f"チャンネル {channel_id} ({guild_id}) の一人以下の状態を確認開始します。メンバー: {member_id}")
        timeout_seconds = await self._get_lonely_timeout_seconds(guild_id)
        logger.debug(f"設定されたタイムアウト時間: {timeout_seconds} 秒")
        await asyncio.sleep(timeout_seconds) # 設定された時間待機

        # 再度チャンネルの状態を確認
        guild = self.bot.get_guild(guild_id)
        if not guild:
            logger.warning(f"ギルド {guild_id} が見つかりませんでした。一人以下の状態確認を終了します。")
            return
        channel = guild.get_channel(channel_id)
        # チャンネルが存在しない、またはタイムアウトしたメンバーがチャンネルにいない場合は処理しない
        if not channel or member_id not in [m.id for m in channel.members]:
            logger.info(f"チャンネル {channel_id} が存在しないか、メンバー {member_id} がチャンネルにいません。一人以下の状態確認を終了します。")
            self.remove_lonely_channel(guild_id, channel_id, cancel_task=False) # タスク自体は完了しているのでキャンセルは不要
            return

        # チャンネルに一人だけ残っている、または複数人だが最初に一人になったメンバーがまだいる場合
        logger.info(f"チャンネル {channel_id} ({guild_id}) が一人以下の状態が継続しています。寝落ち確認メッセージを送信します。")
        # 寝落ち確認メッセージを送信
        notification_channel_id = config.get_notification_channel_id(guild_id) # config から取得
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
                        logger.info(f"寝落ち確認メッセージをチャンネル {notification_channel_id} に送信しました。メッセージID: {message.id}")

                        # リアクション監視タスクを開始
                        reaction_task = asyncio.create_task(self.wait_for_reaction(message.id, member_id, guild_id, channel_id))
                        self.sleep_check_messages[message.id] = {"member_id": member_id, "task": reaction_task}
                        logger.debug(f"リアクション監視タスクを開始しました。メッセージID: {message.id}")

                    except discord.Forbidden:
                        logger.error(f"エラー: チャンネル {notification_channel.name} ({notification_channel_id}) への送信権限がありません。")
                    except Exception as e:
                        logger.error(f"寝落ち確認メッセージ送信中にエラーが発生しました: {e}")
                else:
                     logger.warning(f"メンバー {member_id} が見つかりませんでした。一人以下の状態管理から削除します。")
                     # メンバーが見つからない場合も状態管理から削除
                     key = (guild_id, channel_id)
                     if key in self.lonely_voice_channels:
                        self.lonely_voice_channels.pop(key)
            else:
                logger.warning(f"通知チャンネルが見つかりません: ギルドID {guild_id}。一人以下の状態管理から削除します。")
                # 通知チャンネルがない場合も状態管理から削除
                key = (guild_id, channel_id)
                if key in self.lonely_voice_channels:
                    self.lonely_voice_channels.pop(key)
        else:
            logger.warning(f"ギルド {guild.name} ({guild_id}) の通知チャンネルが設定されていません。寝落ち確認メッセージを送信できません。一人以下の状態管理から削除します。")
            # 通知チャンネルが設定されていない場合も状態管理から削除
            key = (guild_id, channel_id)
            if key in self.lonely_voice_channels:
                self.lonely_voice_channels.pop(key)


    async def wait_for_reaction(self, message_id: int, member_id: int, guild_id: int, channel_id: int):
        logger.info(f"メッセージ {message_id} へのリアクション監視を開始します。メンバー: {member_id}")
        settings = await get_guild_settings(guild_id)
        wait_seconds = settings["reaction_wait_minutes"] * constants.SECONDS_PER_MINUTE
        logger.debug(f"設定されたリアクション待機時間: {wait_seconds} 秒")

        try:
            # 指定された絵文字、ユーザーからのリアクションを待つ
            def check(reaction, user):
                return user.id == member_id and str(reaction.emoji) == constants.REACTION_EMOJI_SLEEP_CHECK and reaction.message.id == message_id

            await self.bot.wait_for('reaction_add', timeout=wait_seconds, check=check)
            logger.info(f"メンバー {member_id} がメッセージ {message_id} に反応しました。ミュート処理をキャンセルします。")
            guild = self.bot.get_guild(guild_id)
            notification_channel_id = config.get_notification_channel_id(guild_id) # config から取得
            if guild and notification_channel_id:
                notification_channel = self.bot.get_channel(notification_channel_id)
                if notification_channel:
                    try:
                        member = guild.get_member(member_id)
                        if member:
                            embed = discord.Embed(title=constants.EMBED_TITLE_SLEEP_CHECK, description=f"{member.mention}{constants.EMBED_DESCRIPTION_SLEEP_CHECK_CANCEL}", color=constants.EMBED_COLOR_SUCCESS)
                            await notification_channel.send(embed=embed)
                            logger.info(f"ミュートキャンセルメッセージをチャンネル {notification_channel.id} に送信しました。")
                    except discord.Forbidden:
                        logger.error(f"エラー: チャンネル {notification_channel.name} ({notification_channel.id}) への送信権限がありません。")
                    except Exception as e:
                        logger.error(f"ミュートキャンセルメッセージ送信中にエラーが発生しました: {e}")


        except asyncio.TimeoutError:
            # タイムアウトした場合、ミュート処理を実行
            logger.info(f"メッセージ {message_id} への反応がありませんでした。メンバー {member_id} をミュートします。")
            guild = self.bot.get_guild(guild_id)
            if guild:
                member = guild.get_member(member_id)
                if member:
                    try:
                        await member.edit(mute=True, deafen=True)
                        logger.info(f"メンバー {member.display_name} ({member.id}) をミュートしました。")
                        # ボットがミュートしたメンバーを記録
                        self.add_bot_muted_member(member.id)

                        notification_channel_id = config.get_notification_channel_id(guild_id) # config から取得
                        if notification_channel_id:
                            notification_channel = self.bot.get_channel(notification_channel_id)
                            if notification_channel:
                                try:
                                    embed = discord.Embed(title=constants.EMBED_TITLE_SLEEP_CHECK, description=f"{member.mention}{constants.EMBED_DESCRIPTION_SLEEP_CHECK_MUTE}", color=constants.EMBED_COLOR_ERROR)
                                    await notification_channel.send(embed=embed)
                                    logger.info(f"ミュート実行メッセージをチャンネル {notification_channel.id} に送信しました。")
                                except discord.Forbidden:
                                    logger.error(f"エラー: チャンネル {notification_channel.name} ({notification_channel.id}) への送信権限がありません。")
                                except Exception as e:
                                    logger.error(f"ミュート実行メッセージ送信中にエラーが発生しました: {e}")

                    except discord.Forbidden:
                        logger.error(f"エラー: メンバー {member.display_name} ({member_id}) のミュートを解除する権限がありません。")
                    except Exception as e:
                        logger.error(f"メンバーミュート解除中にエラーが発生しました: {e}")
                else:
                    logger.warning(f"メンバー {member_id} が見つかりませんでした。")
            else:
                logger.warning(f"ギルド {guild_id} が見つかりませんでした。")

        finally:
            # 処理が完了したら、一時的な記録から削除
            if message_id in self.sleep_check_messages:
                self.sleep_check_messages.pop(message_id)
                logger.debug(f"メッセージ {message_id} をsleep_check_messagesから削除しました。")
            # チャンネルの状態管理からも削除（ミュートされたか反応があったかで一人以下の状態は終了とみなす）
            key = (guild_id, channel_id)
            if key in self.lonely_voice_channels:
                 self.lonely_voice_channels.pop(key)
                 logger.debug(f"チャンネル {channel_id} ({guild_id}) をlonely_voice_channelsから削除しました。")

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
    async def _check_and_notify_milestone(self, member: discord.Member, guild: discord.Guild, before_total: float, after_total: float):
        logger.info(f"マイルストーン通知を確認します。メンバー: {member.id}, ギルド: {guild.id}, Before: {before_total}, After: {after_total}")
        # config モジュールから get_notification_channel_id をインポートする必要があります
        guild_id = str(guild.id)
        notification_channel_id = config.get_notification_channel_id(guild.id) # config から取得

        if notification_channel_id is None:
            logger.debug(f"ギルド {guild_id} の通知先チャンネルが設定されていません。マイルストーン通知はスキップします。")
            return # 通知先チャンネルが設定されていない場合は何もしない

        notification_channel = self.bot.get_channel(notification_channel_id)
        if not notification_channel:
            logger.warning(f"通知チャンネルが見つかりません: ギルドID {guild_id}, チャンネルID {notification_channel_id}")
            return

        hour_threshold = constants.MILESTONE_THRESHOLD_SECONDS
        before_milestone = int(before_total // hour_threshold)
        after_milestone = int(after_total // hour_threshold)
        logger.debug(f"Before milestone: {before_milestone}, After milestone: {after_milestone}")

        if after_milestone > before_milestone:
            achieved_hours = after_milestone * 10 # 10時間ごとのマイルストーンなので 10 を乗算
            logger.info(f"メンバー {member.id} が {achieved_hours} 時間のマイルストーンを達成しました。")
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
                logger.info(f"マイルストーン通知をチャンネル {notification_channel_id} に送信しました。")
            except discord.Forbidden:
                logger.error(f"エラー: チャンネル {notification_channel.name} ({notification_channel_id}) への送信権限がありません。")
            except Exception as e:
                logger.error(f"通知送信中にエラーが発生しました: {e}")
        else:
            logger.debug("マイルストーン達成なし。")

    # チャンネルに入室した場合の処理
    async def _handle_join(self, member, channel_after):
        logger.info(f"メンバー {member.id} がチャンネル {channel_after.id} ({channel_after.name}) に入室しました。")
        guild_id = member.guild.id
        key_after = (guild_id, channel_after.id)

        # 入室したチャンネルが一人以下になった場合、そのメンバーに対して一人以下の状態を開始
        if len(channel_after.members) == 1:
            lonely_member = channel_after.members[0]
            if key_after not in self.sleep_check_manager.lonely_voice_channels and lonely_member.id not in self.sleep_check_manager.bot_muted_members:
                logger.debug(f"チャンネル {channel_after.id} ({guild_id}) が一人以下になりました。寝落ち確認を開始します。メンバー: {lonely_member.id}")
                task = asyncio.create_task(self.sleep_check_manager.check_lonely_channel(guild_id, channel_after.id, lonely_member.id))
                self.sleep_check_manager.add_lonely_channel(guild_id, channel_after.id, lonely_member.id, task)
        # 入室したチャンネルが複数人になった場合、一人以下の状態を解除
        elif len(channel_after.members) > 1:
            if key_after in self.sleep_check_manager.lonely_voice_channels:
                logger.debug(f"チャンネル {channel_after.id} ({guild_id}) に複数人が入室しました。一人以下の状態を解除します。")
                self.sleep_check_manager.remove_lonely_channel(guild_id, channel_after.id)

        # VoiceStateManager に処理を委譲
        await self.voice_state_manager.handle_member_join(member, channel_after)
        logger.debug(f"VoiceStateManager.handle_member_join 処理完了。メンバー: {member.id}, チャンネル: {channel_after.id}")

        # ボットによってミュートされたメンバーが再入室した場合、ミュートを解除
        if member.id in self.sleep_check_manager.bot_muted_members:
            logger.info(f"ボットによってミュートされたメンバー {member.id} が再入室しました。ミュート解除をスケジュールします。")
            async def unmute_after_delay(m: discord.Member):
                logger.debug(f"メンバー {m.id} のミュート解除遅延処理を開始します。")
                # チャンネルの状態変化が完全に反映されるのを待つため、少し遅延させる
                await asyncio.sleep(constants.UNMUTE_DELAY_SECONDS)
                try:
                    await m.edit(mute=False, deafen=False)
                    self.sleep_check_manager.remove_bot_muted_member(m.id)
                    logger.info(f"メンバー {m.display_name} ({m.id}) が再入室したためミュートを解除しました。")

                    notification_channel_id = config.get_notification_channel_id(m.guild.id)
                    if notification_channel_id:
                        notification_channel = self.bot.get_channel(notification_channel_id)
                        if notification_channel:
                            try:
                                embed = discord.Embed(title=constants.EMBED_TITLE_SLEEP_CHECK, description=f"{m.mention}{constants.EMBED_DESCRIPTION_UNMUTE_ON_REJOIN}", color=constants.EMBED_COLOR_SUCCESS)
                                await notification_channel.send(embed=embed)
                                logger.info(f"再入室時ミュート解除メッセージをチャンネル {notification_channel.id} に送信しました。")
                            except discord.Forbidden:
                                logger.error(f"エラー: チャンネル {notification_channel.name} ({notification_channel.id}) への送信権限がありません。")
                            except Exception as e:
                                logger.error(f"再入室時ミュート解除メッセージ送信中にエラーが発生しました: {e}")

                except discord.Forbidden:
                    logger.error(f"エラー: メンバー {m.display_name} ({m.id}) のミュートを解除する権限がありません。")
                except Exception as e:
                    logger.error(f"メンバーミュート解除中にエラーが発生しました: {e}")
                logger.debug(f"メンバー {m.id} のミュート解除遅延処理が完了しました。")

            asyncio.create_task(unmute_after_delay(member))

    # チャンネルから退出した場合の処理
    async def _handle_leave(self, member, channel_before):
        logger.info(f"メンバー {member.id} がチャンネル {channel_before.id} ({channel_before.name}) から退出しました。")
        guild_id = member.guild.id
        key_before = (guild_id, channel_before.id)

        # 退室したメンバーに関連付けられた寝落ち確認タスクがあればキャンセル
        message_ids_to_remove = []
        for message_id, data in self.sleep_check_manager.sleep_check_messages.items():
            if data["member_id"] == member.id:
                if data["task"] and not data["task"].cancelled():
                    data["task"].cancel()
                    logger.info(f"メンバー {member.id} の退出により、メッセージ {message_id} のリアクション監視タスクをキャンセルしました。")
                message_ids_to_remove.append(message_id)

        for message_id in message_ids_to_remove:
            self.sleep_check_manager.sleep_check_messages.pop(message_id)
            logger.debug(f"メッセージ {message_id} をsleep_check_messagesから削除しました。")

        # 退室したチャンネルに誰もいなくなった場合、一人以下の状態を解除
        if channel_before is not None and len(channel_before.members) == 0:
            if key_before in self.sleep_check_manager.lonely_voice_channels:
                logger.debug(f"チャンネル {channel_before.id} ({guild_id}) に誰もいなくなりました。一人以下の状態を解除します。")
                self.sleep_check_manager.remove_lonely_channel(guild_id, channel_before.id)
        # 退室したチャンネルに一人だけ残った場合、そのメンバーに対して一人以下の状態を開始
        elif channel_before is not None and len(channel_before.members) == 1:
            lonely_member = channel_before.members[0]
            if key_before not in self.sleep_check_manager.lonely_voice_channels and lonely_member.id not in self.sleep_check_manager.bot_muted_members:
                logger.debug(f"チャンネル {channel_before.id} ({guild_id}) に一人だけ残りました。寝落ち確認を開始します。メンバー: {lonely_member.id}")
                task = asyncio.create_task(self.sleep_check_manager.check_lonely_channel(guild_id, channel_before.id, lonely_member.id))
                self.sleep_check_manager.add_lonely_channel(guild_id, channel_before.id, lonely_member.id, task)

        # VoiceStateManager に処理を委譲し、統計更新が必要なデータを取得
        ended_sessions_data = await self.voice_state_manager.handle_member_leave(member, channel_before)
        logger.debug(f"VoiceStateManager.handle_member_leave 処理完了。メンバー: {member.id}, チャンネル: {channel_before.id}. 終了セッション数: {len(ended_sessions_data)}")
        # VoiceStateManager から統計更新が必要なデータが返された場合、各メンバーごとに処理
        for member_id, duration, join_time in ended_sessions_data:
            logger.debug(f"統計更新処理を開始します。メンバー: {member_id}, 期間: {duration}, 参加時刻: {join_time}")
            before_total = await get_total_call_time(member_id)
            month_key = join_time.strftime("%Y-%m")
            await update_member_monthly_stats(month_key, member_id, duration)
            after_total = await get_total_call_time(member_id)
            logger.debug(f"メンバー {member_id} の月間統計を更新しました。Before Total: {before_total}, After Total: {after_total}")
            m_obj = member.guild.get_member(member_id) if member.guild else None
            if m_obj:
                await self._check_and_notify_milestone(m_obj, member.guild, before_total, after_total)

    # チャンネル間を移動した場合の処理
    async def _handle_move(self, member, channel_before, channel_after):
        logger.info(f"メンバー {member.id} がチャンネル {channel_before.id} ({channel_before.name}) からチャンネル {channel_after.id} ({channel_after.name}) に移動しました。")
        guild_id = member.guild.id
        key_before = (guild_id, channel_before.id)
        key_after = (guild_id, channel_after.id)

        # 移動元チャンネルに誰もいなくなった場合、一人以下の状態を解除
        if len(channel_before.members) == 0:
            if key_before in self.sleep_check_manager.lonely_voice_channels:
                logger.debug(f"移動元チャンネル {channel_before.id} ({guild_id}) に誰もいなくなりました。一人以下の状態を解除します。")
                self.sleep_check_manager.remove_lonely_channel(guild_id, channel_before.id)
        # 移動元チャンネルに一人だけ残った場合、そのメンバーに対して一人以下の状態を開始
        elif len(channel_before.members) == 1:
            lonely_member = channel_before.members[0]
            if key_before not in self.sleep_check_manager.lonely_voice_channels and lonely_member.id not in self.sleep_check_manager.bot_muted_members:
                logger.debug(f"移動元チャンネル {channel_before.id} ({guild_id}) に一人だけ残りました。寝落ち確認を開始します。メンバー: {lonely_member.id}")
                task = asyncio.create_task(self.sleep_check_manager.check_lonely_channel(guild_id, channel_before.id, lonely_member.id))
                self.sleep_check_manager.add_lonely_channel(guild_id, channel_before.id, lonely_member.id, task)

        # 移動先チャンネルが一人以下になった場合、そのメンバーに対して一人以下の状態を開始
        if len(channel_after.members) == 1:
            lonely_member = channel_after.members[0]
            if key_after not in self.sleep_check_manager.lonely_voice_channels and lonely_member.id not in self.sleep_check_manager.bot_muted_members:
                logger.debug(f"移動先チャンネル {channel_after.id} ({guild_id}) が一人以下になりました。寝落ち確認を開始します。メンバー: {lonely_member.id}")
                task = asyncio.create_task(self.sleep_check_manager.check_lonely_channel(guild_id, channel_after.id, lonely_member.id))
                self.sleep_check_manager.add_lonely_channel(guild_id, channel_after.id, lonely_member.id, task)
        # 移動先チャンネルが複数人になった場合、一人以下の状態を解除
        elif len(channel_after.members) > 1:
            if key_after in self.sleep_check_manager.lonely_voice_channels:
                logger.debug(f"移動先チャンネル {channel_after.id} ({guild_id}) に複数人が入室しました。一人以下の状態を解除します。")
                self.sleep_check_manager.remove_lonely_channel(guild_id, channel_after.id)

        # VoiceStateManager に処理を委譲し、統計更新が必要なデータを取得
        ended_sessions_from_before, joined_session_data = await self.voice_state_manager.handle_member_move(member, channel_before, channel_after)
        logger.debug(f"VoiceStateManager.handle_member_move 処理完了。メンバー: {member.id}, 移動元: {channel_before.id}, 移動先: {channel_after.id}. 終了セッション数: {len(ended_sessions_from_before)}, 参加セッションデータ: {joined_session_data is not None}")

        # 移動元での退出による統計更新とマイルストーン通知
        for member_id_leave, duration_leave, join_time_leave in ended_sessions_from_before:
            logger.debug(f"移動元退出による統計更新処理を開始します。メンバー: {member_id_leave}, 期間: {duration_leave}, 参加時刻: {join_time_leave}")
            before_total_leave = await get_total_call_time(member_id_leave)
            month_key_leave = join_time_leave.strftime("%Y-%m")
            await update_member_monthly_stats(month_key_leave, member_id_leave, duration_leave)
            after_total_leave = await get_total_call_time(member_id_leave)
            logger.debug(f"メンバー {member_id_leave} の月間統計を更新しました。Before Total: {before_total_leave}, After Total: {after_total_leave}")
            m_obj_leave = member.guild.get_member(member_id_leave) if member.guild else None
            if m_obj_leave:
                 await self._check_and_notify_milestone(m_obj_leave, member.guild, before_total_leave, after_total_leave)

        # 移動先での入室による統計更新とマイルストーン通知 (移動してきたメンバー自身の場合のみ)
        if joined_session_data is not None:
             member_id_join, duration_join, join_time_join = joined_session_data
             logger.debug(f"移動先入室による統計更新処理を開始します。メンバー: {member_id_join}, 期間: {duration_join}, 参加時刻: {join_time_join}")
             before_total_join = await get_total_call_time(member_id_join)
             month_key_join = join_time_join.strftime("%Y-%m")
             # 移動直後は通話時間0として記録（新しいセッションの開始）
             await update_member_monthly_stats(month_key_join, member_id_join, 0)
             after_total_join = await get_total_call_time(member_id_join)
             logger.debug(f"メンバー {member_id_join} の月間統計を更新しました。Before Total: {before_total_join}, After Total: {after_total_join}")
             m_obj_join = member.guild.get_member(member_id_join) if member.guild else None
             if m_obj_join:
                 await self._check_and_notify_milestone(m_obj_join, member.guild, before_total_join, after_total_join)


        # ボットによってミュートされたメンバーがチャンネル移動した場合、ミュートを解除
        if member.id in self.sleep_check_manager.bot_muted_members:
            logger.info(f"ボットによってミュートされたメンバー {member.id} がチャンネル移動しました。ミュート解除をスケジュールします。")
            async def unmute_after_delay(m: discord.Member):
                logger.debug(f"メンバー {m.id} のミュート解除遅延処理を開始します。")
                await asyncio.sleep(constants.UNMUTE_DELAY_SECONDS) # 1秒待機
                try:
                    await m.edit(mute=False, deafen=False)
                    self.sleep_check_manager.remove_bot_muted_member(m.id)
                    logger.info(f"メンバー {m.display_name} ({m.id}) がチャンネル移動したためミュートを解除しました。")

                    notification_channel_id = config.get_notification_channel_id(m.guild.id)
                    if notification_channel_id:
                        notification_channel = self.bot.get_channel(notification_channel_id)
                        if notification_channel:
                            try:
                                embed = discord.Embed(title=constants.EMBED_TITLE_SLEEP_CHECK, description=f"{m.mention}{constants.EMBED_DESCRIPTION_UNMUTE_ON_REJOIN}", color=constants.EMBED_COLOR_SUCCESS)
                                await notification_channel.send(embed=embed)
                                logger.info(f"チャンネル移動時ミュート解除メッセージをチャンネル {notification_channel.id} に送信しました。")
                            except discord.Forbidden:
                                logger.error(f"エラー: チャンネル {notification_channel.name} ({notification_channel.id}) への送信権限がありません。")
                            except Exception as e:
                                logger.error(f"チャンネル移動時ミュート解除メッセージ送信中にエラーが発生しました: {e}")

                except discord.Forbidden:
                    logger.error(f"エラー: メンバー {m.display_name} ({m.id}) のミュートを解除する権限がありません。")
                except Exception as e:
                    logger.error(f"メンバーミュート解除中にエラーが発生しました: {e}")
                logger.debug(f"メンバー {m.id} のミュート解除遅延処理が完了しました。")

            asyncio.create_task(unmute_after_delay(member))

    # 同一チャンネル内での状態変化（ミュート、デフなど）の処理
    async def _handle_state_change(self, member, before, after):
        logger.debug(f"メンバー {member.id} の同一チャンネル内での状態変化を検出しました。チャンネル: {before.channel.id}")
        # このメソッドは、同一チャンネル内でのミュート、デフ、ストリーム開始/終了などの状態変化を処理するために存在します。
        # 現在の要件ではこれらの状態変化に対して特別なアクションは必要ないため、passしています。
        # 将来的にこれらの状態変化に対応する機能を追加する場合に、このメソッド内にロジックを記述します。
        pass

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        logger.info(f"on_voice_state_update イベント発生: メンバー {member.id}, Before: {before.channel}, After: {after.channel}")
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

        # --- 2人以上通話状態の記録（各メンバーごとに個別記録＋全参加者リストを維持する処理） ---
        # この部分は VoiceStateManager に移動したので削除
        pass
