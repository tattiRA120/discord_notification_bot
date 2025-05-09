import discord
from discord.ext import tasks
import datetime
from zoneinfo import ZoneInfo
import logging # logging モジュールをインポート

from database import record_voice_session_to_db
from formatters import format_duration, convert_utc_to_jst
from config import get_notification_channel_id
import constants # constants モジュールをインポート

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
        logger.debug("call_sessions 辞書を初期化しました。")

        # (guild_id, channel_id) をキーに、現在進行中の「2人以上通話セッション」を記録する
        # 値: {"session_start": datetimeオブジェクト, "current_members": {member_id: join_time}, "all_participants": {member_id}}
        # session_start: そのチャンネルで2人以上の通話が開始された時刻
        # current_members: 現在そのチャンネルにいるメンバーとそのチャンネルに参加した時刻
        # all_participants: そのセッション中に一度でもチャンネルに参加した全てのメンバーIDのセット
        self.active_voice_sessions = {}
        logger.debug("active_voice_sessions 辞書を初期化しました。")

        # 2人以上が通話中のチャンネルを追跡するセット
        # 要素: (guild_id, channel_id)
        self.active_status_channels = set()
        logger.debug("active_status_channels セットを初期化しました。")

        # ボットのステータスを通話時間で更新するタスク
        self.update_call_status_task = tasks.loop(seconds=constants.STATUS_UPDATE_INTERVAL_SECONDS)(self._update_call_status_task)
        logger.debug(f"ステータス更新タスクを {constants.STATUS_UPDATE_INTERVAL_SECONDS} 秒間隔で設定しました。")

    # --- ボイスステート更新ハンドラ ---
    async def handle_member_join(self, member: discord.Member, channel_after: discord.VoiceChannel):
        """
        メンバーがボイスチャンネルに参加した時に呼び出されるハンドラ。
        通話通知機能と2人以上通話状態の記録を処理します。
        """
        logger.info(f"handle_member_join: メンバー {member.id} がチャンネル {channel_after.id} ({channel_after.name}) に参加しました。")
        guild_id = member.guild.id
        now = datetime.datetime.now(datetime.timezone.utc)
        key = (guild_id, channel_after.id)
        logger.debug(f"ギルドID: {guild_id}, チャンネルID: {channel_after.id}")

        # 通話通知機能 (入室時)
        # ギルド内でそのチャンネルでの通話が開始された最初のメンバーであれば通知を送信します。
        if guild_id not in self.call_sessions:
            self.call_sessions[guild_id] = {}
            logger.debug(f"ギルド {guild_id} の call_sessions エントリを作成しました。")
        if channel_after.id not in self.call_sessions[guild_id]:
             # 新しい通話セッションの開始時刻と最初のメンバーを記録
             start_time = now
             self.call_sessions[guild_id][channel_after.id] = {"start_time": start_time, "first_member": member.id}
             logger.info(f"チャンネル {channel_after.id} ({guild_id}) で新しい通話セッションを開始しました。開始時刻: {start_time}, 最初のメンバー: {member.id}")
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
                         logger.info(f"通話開始通知をチャンネル {notification_channel_id} に送信しました。")
                     except discord.Forbidden:
                         logger.error(f"エラー: チャンネル {notification_channel.name} ({notification_channel_id}) への送信権限がありません。")
                     except Exception as e:
                         logger.error(f"通話開始通知送信中にエラーが発生しました: {e}")
                 else:
                     # 通知チャンネルが見つからない場合のログ出力
                     logger.warning(f"通知チャンネルが見つかりません:ギルドID {guild_id}")
             else:
                 logger.info(f"ギルド {guild_id} の通知チャンネルが設定されていません。通話開始通知は送信されません。")
        else:
            logger.debug(f"チャンネル {channel_after.id} ({guild_id}) に既存の通話セッションがあります。通話開始通知はスキップします。")


        # 2人以上通話状態の記録（各メンバーごとに個別記録＋全参加者リストを維持する処理）
        # チャンネル内の人数がconstants.MIN_MEMBERS_FOR_SESSION (現在2) 人以上の場合に、
        # 新しいセッションを開始するか、既存のセッションにメンバーを追加します。
        if len(channel_after.members) >= constants.MIN_MEMBERS_FOR_SESSION:
            logger.debug(f"チャンネル {channel_after.id} ({guild_id}) の人数が {constants.MIN_MEMBERS_FOR_SESSION} 人以上 ({len(channel_after.members)} 人) です。")
            if key not in self.active_voice_sessions:
                logger.info(f"チャンネル {channel_after.id} ({guild_id}) で新しい2人以上通話セッションを開始します。")
                # 新しい2人以上通話セッションを開始
                # セッション開始時刻は、通話がconstants.MIN_MEMBERS_FOR_SESSION人以上になった時刻（この時点の now）
                self.active_voice_sessions[key] = {
                    "session_start": now,
                    "current_members": { m.id: now for m in channel_after.members }, # 現在のメンバーとその参加時刻を記録
                    "all_participants": set(m.id for m in channel_after.members) # 全参加者リストに現在のメンバーを追加
                }
                logger.debug(f"新しい active_voice_sessions エントリを作成しました: {key}")
            else:
                logger.debug(f"チャンネル {channel_after.id} ({guild_id}) に既存の2人以上通話セッションがあります。メンバーリストを更新します。")
                # 既存の2人以上通話セッションがある場合、新たに入室したメンバーを更新する
                session_data = self.active_voice_sessions[key]
                for m in channel_after.members:
                    if m.id not in session_data["current_members"]:
                        session_data["current_members"][m.id] = now # 新規参加メンバーの参加時刻を記録
                        logger.debug(f"メンバー {m.id} を active_voice_sessions[{key}]['current_members'] に追加しました。")
                    session_data["all_participants"].add(m.id) # 全参加者リストにメンバーを追加
                    logger.debug(f"メンバー {m.id} を active_voice_sessions[{key}]['all_participants'] に追加しました。")


            # チャンネルの人数がconstants.MIN_MEMBERS_FOR_SESSION人以上になったら active_status_channels に追加
            if key not in self.active_status_channels:
                self.active_status_channels.add(key)
                logger.debug(f"チャンネル {key} を active_status_channels に追加しました。")
                # 初めて2人以上の通話が始まった場合、ボットのステータス更新タスクを開始
                if not self.update_call_status_task.is_running():
                    self.update_call_status_task.start()
                    logger.info("ボットのステータス更新タスクを開始しました。")

        else:
            logger.debug(f"チャンネル {channel_after.id} ({guild_id}) の人数が {constants.MIN_MEMBERS_FOR_SESSION} 人未満 ({len(channel_after.members)} 人) です。")
            # 人数がconstants.MIN_MEMBERS_FOR_SESSION人未満の場合は、既にセッションが存在する場合のみ更新する
            # （一時的に人数が減ってもセッション自体は継続しているとみなす）
            if key in self.active_voice_sessions:
                logger.debug(f"チャンネル {channel_after.id} ({guild_id}) に既存の2人以上通話セッションがあります。メンバーリストを更新します。")
                session_data = self.active_voice_sessions[key]
                for m in channel_after.members:
                    if m.id not in session_data["current_members"]:
                        session_data["current_members"][m.id] = now # 新規参加メンバーの参加時刻を記録
                        logger.debug(f"メンバー {m.id} を active_voice_sessions[{key}]['current_members'] に追加しました。")
                    session_data["all_participants"].add(m.id) # 全参加者リストにメンバーを追加
                    logger.debug(f"メンバー {m.id} を active_voice_sessions[{key}]['all_participants'] に追加しました。")
            else:
                logger.debug(f"チャンネル {channel_after.id} ({guild_id}) にアクティブな2人以上通話セッションはありません。")


    async def handle_member_leave(self, member: discord.Member, channel_before: discord.VoiceChannel):
        """
        メンバーがボイスチャンネルから退出した時に呼び出されるハンドラ。
        通話通知機能と2人以上通話状態の記録を処理します。
        """
        logger.info(f"handle_member_leave: メンバー {member.id} がチャンネル {channel_before.id} ({channel_before.name}) から退出しました。")
        guild_id = member.guild.id
        now = datetime.datetime.now(datetime.timezone.utc)
        key = (guild_id, channel_before.id)
        logger.debug(f"ギルドID: {guild_id}, チャンネルID: {channel_before.id}")

        # 通話通知機能 (退出時)
        voice_channel_before_id = channel_before.id
        # 退出元のチャンネルでの通話セッションが存在する場合
        if guild_id in self.call_sessions and voice_channel_before_id in self.call_sessions[guild_id]:
            voice_channel = channel_before
            # 退出元のチャンネルに誰もいなくなった場合のみ通話終了とみなし、通知を送信
            if len(voice_channel.members) == 0:
                logger.info(f"チャンネル {voice_channel_before_id} ({guild_id}) に誰もいなくなりました。通話終了とみなします。")
                session = self.call_sessions[guild_id].pop(voice_channel_before_id) # 通話セッションを終了
                start_time = session["start_time"]
                call_duration = (now - start_time).total_seconds() # 通話時間を計算
                duration_str = format_duration(call_duration) # 表示用にフォーマット
                logger.debug(f"通話時間: {duration_str}")
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
                            logger.info(f"通話終了通知をチャンネル {notification_channel_id} に送信しました。")
                        except discord.Forbidden:
                            logger.error(f"エラー: チャンネル {notification_channel.name} ({notification_channel_id}) への送信権限がありません。")
                        except Exception as e:
                            logger.error(f"通話終了通知送信中にエラーが発生しました: {e}")
                    else:
                        # 通知チャンネルが見つからない場合のログ出力
                        logging.warning(f"通知チャンネルが見つかりません:ギルドID {guild_id}")
                else:
                    logger.info(f"ギルド {guild_id} の通知チャンネルが設定されていません。通話終了通知は送信されません。")
            else:
                logger.debug(f"チャンネル {voice_channel_before_id} ({guild_id}) にまだメンバーがいます ({len(voice_channel.members)} 人)。通話終了とはみなしません。")
        else:
            logger.debug(f"チャンネル {voice_channel_before_id} ({guild_id}) にアクティブな通話セッションはありません。")


        # 2人以上通話状態の記録（各メンバーごとに個別記録＋全参加者リストを維持する処理）
        # 退出元のチャンネルで2人以上通話セッションがアクティブな場合
        ended_sessions_data = [] # 終了した個別のメンバーセッションデータを収集するリスト
        if key in self.active_voice_sessions:
            logger.debug(f"チャンネル {channel_before.id} ({guild_id}) にアクティブな2人以上通話セッションがあります。")
            session_data = self.active_voice_sessions[key]

            # もし対象メンバーが現在セッションに在室中ならその個人分の退室処理を実施
            if member.id in session_data["current_members"]:
                logger.debug(f"メンバー {member.id} が active_voice_sessions[{key}]['current_members'] に存在します。個人セッションを終了します。")
                join_time = session_data["current_members"].pop(member.id) # メンバーを現在のメンバーリストから削除
                duration = (now - join_time).total_seconds() # そのメンバーの通話時間を計算
                ended_sessions_data.append((member.id, duration, join_time)) # 終了リストに追加
                logger.debug(f"メンバー {member.id} の個人セッション終了データ: 期間 {duration}, 参加時刻 {join_time}")
            else:
                logger.debug(f"メンバー {member.id} は active_voice_sessions[{key}]['current_members'] に存在しませんでした。")


            # もし退室後、チャンネル内人数がconstants.MIN_MEMBERS_FOR_SESSION (現在2) 人未満になったら
            # そのチャンネルでの2人以上通話セッション全体を終了する処理を実施
            if channel_before is not None and len(channel_before.members) < constants.MIN_MEMBERS_FOR_SESSION:
                logger.info(f"チャンネル {channel_before.id} ({guild_id}) の人数が {constants.MIN_MEMBERS_FOR_SESSION} 人未満 ({len(channel_before.members)} 人) になりました。2人以上通話セッションを終了します。")
                # セッション終了時の残メンバーの統計更新と通知チェック（voice_events.pyで処理）
                remaining_members_data = session_data["current_members"].copy()
                for m_id, join_time in remaining_members_data.items():
                    d = (now - join_time).total_seconds()
                    ended_sessions_data.append((m_id, d, join_time)) # 終了リストに追加
                    logger.debug(f"終了セッションに残っていたメンバー {m_id} のデータ: 期間 {d}, 参加時刻 {join_time}")
                    session_data["current_members"].pop(m_id) # 残メンバーを現在のメンバーリストから削除

                # セッション全体の通話時間を計算し、データベースに記録
                overall_duration = (now - session_data["session_start"]).total_seconds()
                logger.info(f"チャンネル {channel_before.id} ({guild_id}) の2人以上通話セッション全体を記録します。開始時刻: {session_data['session_start']}, 期間: {overall_duration}, 全参加者: {list(session_data['all_participants'])}")
                await record_voice_session_to_db(session_data["session_start"], overall_duration, list(session_data["all_participants"]))
                self.active_voice_sessions.pop(key, None) # アクティブセッションから削除
                logger.debug(f"チャンネル {key} を active_voice_sessions から削除しました。")

                # チャンネルの人数がconstants.MIN_MEMBERS_FOR_SESSION人未満になったら active_status_channels から削除
                self.active_status_channels.discard(key)
                logger.debug(f"チャンネル {key} を active_status_channels から削除しました。")
                # 2人以上の通話がすべて終了した場合、ボットのステータス更新タスクを停止しステータスをクリア
                if not self.active_status_channels and self.update_call_status_task.is_running():
                    self.update_call_status_task.stop()
                    await self.bot.change_presence(activity=None)
                    logger.info("アクティブな2人以上通話チャンネルがなくなったため、ステータス更新タスクを停止しステータスをクリアしました。")
            else:
                logger.debug(f"チャンネル {channel_before.id} ({guild_id}) にまだ {constants.MIN_MEMBERS_FOR_SESSION} 人以上 ({len(channel_before.members)} 人) います。2人以上通話セッションは継続します。")

        return ended_sessions_data # 終了した個別のメンバーセッションデータのリストを返す

    async def handle_member_move(self, member: discord.Member, channel_before: discord.VoiceChannel, channel_after: discord.VoiceChannel):
        """
        メンバーがボイスチャンネル間を移動した時に呼び出されるハンドラ。
        移動元チャンネルからの退出処理と移動先チャンネルへの入室処理を組み合わせます。
        """
        logger.info(f"handle_member_move: メンバー {member.id} がチャンネル {channel_before.id} ({channel_before.name}) からチャンネル {channel_after.id} ({channel_after.name}) に移動しました。")
        guild_id = member.guild.id
        now = datetime.datetime.now(datetime.timezone.utc)
        key_before = (guild_id, channel_before.id)
        key_after = (guild_id, channel_after.id)
        logger.debug(f"ギルドID: {guild_id}, 移動元チャンネルID: {channel_before.id}, 移動先チャンネルID: {channel_after.id}")

        # 通話通知機能 (移動元からの退出処理)
        voice_channel_before_id = channel_before.id
        # 移動元のチャンネルでの通話セッションが存在する場合
        if guild_id in self.call_sessions and voice_channel_before_id in self.call_sessions[guild_id]:
            voice_channel = channel_before
            # 移動元のチャンネルに誰もいなくなった場合のみ通話終了とみなし、通知を送信
            if len(voice_channel.members) == 0:
                logger.info(f"移動元チャンネル {voice_channel_before_id} ({guild_id}) に誰もいなくなりました。通話終了とみなします。")
                session = self.call_sessions[guild_id].pop(voice_channel_before_id) # 通話セッションを終了
                start_time = session["start_time"]
                call_duration = (now - start_time).total_seconds() # 通話時間を計算
                duration_str = format_duration(call_duration) # 表示用にフォーマット
                logger.debug(f"通話時間: {duration_str}")
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
                            logger.info(f"通話終了通知をチャンネル {notification_channel_id} に送信しました。")
                        except discord.Forbidden:
                            logger.error(f"エラー: チャンネル {notification_channel.name} ({notification_channel_id}) への送信権限がありません。")
                        except Exception as e:
                            logger.error(f"通話終了通知送信中にエラーが発生しました: {e}")
                    else:
                        # 通知チャンネルが見つからない場合のログ出力
                        logging.warning(f"通知チャンネルが見つかりません:ギルドID {guild_id}")
                else:
                    logger.info(f"ギルド {guild_id} の通知チャンネルが設定されていません。通話終了通知は送信されません。")
            else:
                logger.debug(f"移動元チャンネル {voice_channel_before_id} ({guild_id}) にまだメンバーがいます ({len(voice_channel.members)} 人)。通話終了とはみなしません。")
        else:
            logger.debug(f"移動元チャンネル {voice_channel_before_id} ({guild_id}) にアクティブな通話セッションはありません。")


        # 移動先チャンネルへの入室処理 (通話通知用)
        voice_channel_after_id = channel_after.id
        if guild_id not in self.call_sessions:
            self.call_sessions[guild_id] = {}
            logger.debug(f"ギルド {guild_id} の call_sessions エントリを作成しました。")
        # 移動先のチャンネルに誰もいない状態から一人になった場合、または最初から一人で通話に参加した場合に通話開始とみなす
        if voice_channel_after_id not in self.call_sessions[guild_id] and len(channel_after.members) == 1:
             logger.info(f"移動先チャンネル {voice_channel_after_id} ({guild_id}) で新しい通話セッションを開始します。")
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
                         logger.info(f"通話開始通知をチャンネル {notification_channel_id} に送信しました。")
                     except discord.Forbidden:
                         logger.error(f"エラー: チャンネル {notification_channel.name} ({notification_channel_id}) への送信権限がありません。")
                     except Exception as e:
                         logger.error(f"通話開始通知送信中にエラーが発生しました: {e}")
                 else:
                     # 通知チャンネルが見つからない場合のログ出力
                     logging.warning(f"通知チャンネルが見つかりません:ギルドID {guild_id}")
             else:
                 logger.info(f"ギルド {guild_id} の通知チャンネルが設定されていません。通話開始通知は送信されません。")
        else:
            logger.debug(f"チャンネル {voice_channel_after_id} ({guild_id}) に既存の通話セッションがあるか、人数が1人ではありません。通話開始通知はスキップします。")


        # 2人以上通話状態の記録（各メンバーごとに個別記録＋全参加者リストを維持する処理）
        # 移動元チャンネルからの退出処理と移動先チャンネルへの入室処理を統合して扱います。

        ended_sessions_from_before = [] # 移動元チャンネルで終了した個別のメンバーセッションデータを収集するリスト
        joined_session_data = None # 移動先チャンネルに参加したメンバーのデータ

        # 移動元チャンネルからの退出処理
        # 移動元チャンネルで2人以上通話セッションがアクティブな場合
        if key_before in self.active_voice_sessions:
            logger.debug(f"移動元チャンネル {channel_before.id} ({guild_id}) にアクティブな2人以上通話セッションがあります。")
            session_data_before = self.active_voice_sessions[key_before]
            # 移動したメンバーが現在セッションに在室中ならその個人分の退室処理を実施
            if member.id in session_data_before["current_members"]:
                logger.debug(f"メンバー {member.id} が active_voice_sessions[{key_before}]['current_members'] に存在します。個人セッションを終了します。")
                join_time_leave = session_data_before["current_members"].pop(member.id) # メンバーを現在のメンバーリストから削除
                duration_leave = (now - join_time_leave).total_seconds() # そのメンバーの通話時間を計算
                ended_sessions_from_before.append((member.id, duration_leave, join_time_leave)) # 終了リストに追加
                logger.debug(f"メンバー {member.id} の個人セッション終了データ: 期間 {duration_leave}, 参加時刻 {join_time_leave}")
            else:
                logger.debug(f"メンバー {member.id} は active_voice_sessions[{key_before}]['current_members'] に存在しませんでした。")


            # 移動元チャンネルの人数がconstants.MIN_MEMBERS_FOR_SESSION (現在2) 人未満になったら
            # そのチャンネルでの2人以上通話セッション全体を終了する処理を実施
            if len(channel_before.members) < constants.MIN_MEMBERS_FOR_SESSION:
                logger.info(f"移動元チャンネル {channel_before.id} ({guild_id}) の人数が {constants.MIN_MEMBERS_FOR_SESSION} 人未満 ({len(channel_before.members)} 人) になりました。2人以上通話セッションを終了します。")
                # セッション終了時の残メンバーの統計更新と通知チェック（voice_events.pyで処理）
                remaining_members_data = session_data_before["current_members"].copy()
                for m_id, join_time in remaining_members_data.items():
                    d = (now - join_time).total_seconds()
                    ended_sessions_from_before.append((m_id, d, join_time)) # 終了リストに追加
                    logger.debug(f"終了セッションに残っていたメンバー {m_id} のデータ: 期間 {d}, 参加時刻 {join_time}")
                    session_data_before["current_members"].pop(m_id) # 残メンバーを現在のメンバーリストから削除

                # セッション全体の通話時間を計算し、データベースに記録
                overall_duration = (now - session_data_before["session_start"]).total_seconds()
                logger.info(f"チャンネル {channel_before.id} ({guild_id}) の2人以上通話セッション全体を記録します。開始時刻: {session_data_before['session_start']}, 期間: {overall_duration}, 全参加者: {list(session_data_before['all_participants'])}")
                await record_voice_session_to_db(session_data_before["session_start"], overall_duration, list(session_data_before["all_participants"]))
                self.active_voice_sessions.pop(key_before, None) # アクティブセッションから削除
                logger.debug(f"チャンネル {key_before} を active_voice_sessions から削除しました。")

                # チャンネルの人数がconstants.MIN_MEMBERS_FOR_SESSION人未満になったら active_status_channels から削除
                self.active_status_channels.discard(key_before)
                logger.debug(f"チャンネル {key_before} を active_status_channels から削除しました。")
                # 2人以上の通話がすべて終了した場合、ボットのステータス更新タスクを停止しステータスをクリア
                if not self.active_status_channels and self.update_call_status_task.is_running():
                    self.update_call_status_task.stop()
                    await self.bot.change_presence(activity=None)
                    logger.info("アクティブな2人以上通話チャンネルがなくなったため、ステータス更新タスクを停止しステータスをクリアしました。")
            else:
                logger.debug(f"移動元チャンネル {channel_before.id} ({guild_id}) にまだ {constants.MIN_MEMBERS_FOR_SESSION} 人以上 ({len(channel_before.members)} 人) います。2人以上通話セッションは継続します。")

        else:
            logger.debug(f"移動元チャンネル {channel_before.id} ({guild_id}) にアクティブな2人以上通話セッションはありませんでした。")


        # 移動先チャンネルへの入室処理
        # 移動先チャンネルの人数がconstants.MIN_MEMBERS_FOR_SESSION (現在2) 人以上の場合
        if len(channel_after.members) >= constants.MIN_MEMBERS_FOR_SESSION:
            logger.debug(f"移動先チャンネル {channel_after.id} ({guild_id}) の人数が {constants.MIN_MEMBERS_FOR_SESSION} 人以上 ({len(channel_after.members)} 人) です。")
            if key_after not in self.active_voice_sessions:
                logger.info(f"移動先チャンネル {channel_after.id} ({guild_id}) で新しい2人以上通話セッションを開始します。")
                # 新しい2人以上通話セッションを開始
                self.active_voice_sessions[key_after] = {
                    "session_start": now,
                    "current_members": { m.id: now for m in channel_after.members }, # 現在のメンバーとその参加時刻を記録
                    "all_participants": set(m.id for m in channel_after.members) # 全参加者リストに現在のメンバーを追加
                }
                logger.debug(f"新しい active_voice_sessions エントリを作成しました: {key_after}")
            else:
                logger.debug(f"移動先チャンネル {channel_after.id} ({guild_id}) に既存の2人以上通話セッションがあります。メンバーリストを更新します。")
                # 既存の2人以上通話セッションがある場合、新たに入室したメンバーを更新する
                session_data_after = self.active_voice_sessions[key_after]
                for m in channel_after.members:
                    if m.id not in session_data_after["current_members"]:
                        session_data_after["current_members"][m.id] = now # 新規参加メンバーの参加時刻を記録
                        logger.debug(f"メンバー {m.id} を active_voice_sessions[{key_after}]['current_members'] に追加しました。")
                        if m.id == member.id: # 移動してきたメンバーの場合
                            # 移動してきたメンバーのデータとしてID、現在の通話時間（この時点では0）、参加時刻を記録
                            joined_session_data = (m.id, 0, now)
                            logger.debug(f"移動してきたメンバー {m.id} の joined_session_data を記録しました。")
                    session_data_after["all_participants"].add(m.id) # 全参加者リストにメンバーを追加
                    logger.debug(f"メンバー {m.id} を active_voice_sessions[{key_after}]['all_participants'] に追加しました。")


            # チャンネルの人数がconstants.MIN_MEMBERS_FOR_SESSION人以上になったら active_status_channels に追加
            if key_after not in self.active_status_channels:
                self.active_status_channels.add(key_after)
                logger.debug(f"チャンネル {key_after} を active_status_channels に追加しました。")
                # 初めて2人以上の通話が始まった場合、ボットのステータス更新タスクを開始
                if not self.update_call_status_task.is_running():
                    self.update_call_status_task.start()
                    logger.info("ボットのステータス更新タスクを開始しました。")
        else:
            logger.debug(f"移動先チャンネル {channel_after.id} ({guild_id}) の人数が {constants.MIN_MEMBERS_FOR_SESSION} 人未満 ({len(channel_after.members)} 人) です。")
            # 移動先チャンネルの人数がconstants.MIN_MEMBERS_FOR_SESSION人未満の場合は、既にセッションが存在する場合のみ更新する
            # （一時的に人数が減ってもセッション自体は継続しているとみなす）
            if key_after in self.active_voice_sessions:
                logger.debug(f"移動先チャンネル {channel_after.id} ({guild_id}) に既存の2人以上通話セッションがあります。メンバーリストを更新します。")
                session_data_after = self.active_voice_sessions[key_after]
                for m in channel_after.members:
                    if m.id not in session_data_after["current_members"]:
                        session_data_after["current_members"][m.id] = now # 新規参加メンバーの参加時刻を記録
                        logger.debug(f"メンバー {m.id} を active_voice_sessions[{key_after}]['current_members'] に追加しました。")
                        if m.id == member.id: # 移動してきたメンバーの場合
                            # 移動してきたメンバーのデータとしてID、現在の通話時間（この時点では0）、参加時刻を記録
                            joined_session_data = (m.id, 0, now)
                            logger.debug(f"移動してきたメンバー {m.id} の joined_session_data を記録しました。")
                    session_data_after["all_participants"].add(m.id) # 全参加者リストにメンバーを追加
                    logger.debug(f"メンバー {m.id} を active_voice_sessions[{key_after}]['all_participants'] に追加しました。")
            else:
                logger.debug(f"移動先チャンネル {channel_after.id} ({guild_id}) にアクティブな2人以上通話セッションはありません。")


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
        logger.debug(f"開始時刻 {start_time} からの経過時間: {duration} 秒")
        return duration

    # アクティブな通話チャンネルとその通話時間を取得する
    def get_active_call_durations(self, guild_id: int):
        """
        指定されたギルドのアクティブな2人以上通話チャンネルとその通話時間を取得し、
        表示用にフォーマットして返します。/call_duration コマンドで使用されます。
        """
        logger.info(f"ギルド {guild_id} のアクティブな通話チャンネルの通話時間を取得します。")
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
                    logger.debug(f"アクティブな通話チャンネル: {channel.name}, 通話時間: {formatted_duration}")
                else:
                    logger.warning(f"アクティブセッションのチャンネル {key[1]} ({key[0]}) が見つからないかボイスチャンネルではありません。")
        logger.info(f"ギルド {guild_id} のアクティブな通話チャンネル数: {len(active_calls)}")
        # アクティブな通話チャンネルとその通話時間のリストを返す
        return active_calls

    # --- ステータス通話時間更新タスク ---
    async def _update_call_status_task(self):
        """
        ボットのステータスを現在アクティブな2人以上通話チャンネルの通話時間で更新するタスク。
        constants.STATUS_UPDATE_INTERVAL_SECONDS 間隔で実行されます。
        """
        logger.debug("ステータス更新タスクを実行します。")
        # 2人以上通話中のチャンネルがあるか確認
        if self.active_status_channels:
            logger.debug(f"アクティブなステータスチャンネルがあります: {self.active_status_channels}")
            # active_status_channelsからステータスに表示するチャンネルを一つ選択（セットなので順序は保証されない）
            channel_key_to_display = next(iter(self.active_status_channels))
            guild_id, channel_id = channel_key_to_display
            guild = self.bot.get_guild(guild_id)
            channel = self.bot.get_channel(channel_id)

            # ギルド、チャンネルが存在し、かつそのチャンネルがまだアクティブセッションリストにあるか確認
            if guild and channel and channel_key_to_display in self.active_voice_sessions:
                logger.debug(f"ステータスに表示するチャンネル: {channel.name} ({guild.name})")
                # 選択したチャンネルの通話時間を計算
                session_data = self.active_voice_sessions[channel_key_to_display]
                duration_seconds = self.calculate_call_duration_seconds(session_data["session_start"])
                # 表示用にフォーマット
                formatted_duration = format_duration(duration_seconds)
                # ボットのカスタムステータスを設定
                activity = discord.CustomActivity(name=f"{channel.name}: {formatted_duration}")
                await self.bot.change_presence(activity=activity)
                logger.info(f"ボットのステータスを更新しました: {activity.name}")
            else:
                logger.warning(f"ステータス表示対象チャンネル {channel_key_to_display} が見つからないか、アクティブセッションにありません。")
                # チャンネルが見つからない、またはactive_voice_sessionsにない場合は
                # active_status_channels から削除し、2人以上通話がすべて終了していればステータスをクリア
                self.active_status_channels.discard(channel_key_to_display)
                logger.debug(f"チャンネル {channel_key_to_display} を active_status_channels から削除しました。")
                if not self.active_status_channels:
                     await self.bot.change_presence(activity=None)
                     logger.info("アクティブなステータスチャンネルがなくなったため、ステータスをクリアしました。")
        else:
            logger.debug("アクティブなステータスチャンネルがありません。ステータスをクリアします。")
            # 2人以上の通話がない場合はボットのステータスをクリア
            await self.bot.change_presence(activity=None)
            logger.info("ボットのステータスをクリアしました。")

    async def record_session_end(self, guild_id: int, channel_id: int):
        """
        特定のチャンネルでの2人以上通話セッションが終了した時の処理。
        セッション全体の通話時間をデータベースに記録し、アクティブセッションリストから削除します。
        終了した個別のメンバーセッションデータを返します（統計更新と通知チェックは voice_events.py で行います）。
        """
        logger.info(f"チャンネル {channel_id} ({guild_id}) の2人以上通話セッション終了処理を開始します。")
        key = (guild_id, channel_id)
        ended_sessions_data = [] # 終了した個別のメンバーセッションデータを収集するリスト

        # 指定されたチャンネルの2人以上通話セッションがアクティブな場合
        if key in self.active_voice_sessions:
            session_data = self.active_voice_sessions[key]
            now = datetime.datetime.now(datetime.timezone.utc)
            logger.debug(f"チャンネル {key} にアクティブなセッションが見つかりました。")

            # セッション終了時の残メンバーの統計更新と通知チェックのためにデータを収集
            remaining_members_data = session_data["current_members"].copy()
            for m_id, join_time in remaining_members_data.items():
                d = (now - join_time).total_seconds()
                ended_sessions_data.append((m_id, d, join_time)) # 終了リストに追加
                logger.debug(f"終了セッションに残っていたメンバー {m_id} のデータ: 期間 {d}, 参加時刻 {join_time}")
                session_data["current_members"].pop(m_id) # 残メンバーを現在のメンバーリストから削除
                logger.debug(f"メンバー {m_id} を active_voice_sessions[{key}]['current_members'] から削除しました。")


            # セッション全体の通話時間を計算し、データベースに記録
            overall_duration = (now - session_data["session_start"]).total_seconds()
            logger.info(f"チャンネル {channel_id} ({guild_id}) の2人以上通話セッション全体を記録します。開始時刻: {session_data['session_start']}, 期間: {overall_duration}, 全参加者: {list(session_data['all_participants'])}")
            await record_voice_session_to_db(session_data["session_start"], overall_duration, list(session_data["all_participants"]))
            self.active_voice_sessions.pop(key, None) # アクティブセッションから削除
            logger.debug(f"チャンネル {key} を active_voice_sessions から削除しました。")

            # チャンネルの人数が1人以下になったら active_status_channels から削除
            self.active_status_channels.discard(key)
            logger.debug(f"チャンネル {key} を active_status_channels から削除しました。")
            # 2人以上の通話がすべて終了した場合、ボットのステータス更新タスクを停止しステータスをクリア
            if not self.active_status_channels and self.update_call_status_task.is_running():
                self.update_call_status_task.stop()
                await self.bot.change_presence(activity=None)
                logger.info("アクティブな2人以上通話チャンネルがなくなったため、ステータス更新タスクを停止しステータスをクリアしました。")
        else:
            logger.warning(f"チャンネル {channel_id} ({guild_id}) にアクティブな2人以上通話セッションはありませんでした。終了処理はスキップします。")

        return ended_sessions_data # 終了した個別のメンバーセッションデータのリストを返す


    def start_session(self, guild_id: int, channel_id: int, members: list[discord.Member]):
        """
        新しい2人以上通話セッションを開始します。
        voice_events.py でチャンネルにconstants.MIN_MEMBERS_FOR_SESSION人以上になった時に呼び出されます。
        """
        logger.info(f"チャンネル {channel_id} ({guild_id}) で新しい2人以上通話セッションを開始します。")
        key = (guild_id, channel_id)
        now = datetime.datetime.now(datetime.timezone.utc)
        # 新しいセッションデータを初期化
        self.active_voice_sessions[key] = {
            "session_start": now, # セッション開始時刻
            "current_members": { m.id: now for m in members }, # 現在のメンバーとその参加時刻
            "all_participants": set(m.id for m in members) # 全参加者リスト
        }
        logger.debug(f"新しい active_voice_sessions エントリを作成しました: {key}")
        # active_status_channels にチャンネルを追加
        self.active_status_channels.add(key)
        logger.debug(f"チャンネル {key} を active_status_channels に追加しました。")
        # ステータス更新タスクが実行中でなければ開始
        if not self.update_call_status_task.is_running():
            self.update_call_status_task.start()
            logger.info("ボットのステータス更新タスクを開始しました。")

    def update_session_members(self, guild_id: int, channel_id: int, members: list[discord.Member]):
        """
        既存の2人以上通話セッションのメンバーリストを更新します。
        チャンネルにメンバーが追加された際に呼び出され、新規参加メンバーをセッションに追加します。
        """
        logger.info(f"チャンネル {channel_id} ({guild_id}) の2人以上通話セッションのメンバーリストを更新します。")
        key = (guild_id, channel_id)
        now = datetime.datetime.now(datetime.timezone.utc)
        # 指定されたチャンネルの2人以上通話セッションがアクティブな場合
        if key in self.active_voice_sessions:
            logger.debug(f"チャンネル {key} にアクティブなセッションが見つかりました。")
            session_data = self.active_voice_sessions[key]
            # チャンネルにいる各メンバーを確認
            for m in members:
                # まだセッションのcurrent_membersリストにいないメンバーであれば追加
                if m.id not in session_data["current_members"]:
                    session_data["current_members"][m.id] = now # 参加時刻を記録
                    logger.debug(f"メンバー {m.id} を active_voice_sessions[{key}]['current_members'] に追加しました。")
                session_data["all_participants"].add(m.id) # 全参加者リストに追加
                logger.debug(f"メンバー {m.id} を active_voice_sessions[{key}]['all_participants'] に追加しました。")
            logger.debug(f"チャンネル {key} のメンバーリスト更新が完了しました。現在のメンバー: {list(session_data['current_members'].keys())}, 全参加者: {list(session_data['all_participants'])}")
        else:
            logger.warning(f"チャンネル {channel_id} ({guild_id}) にアクティブな2人以上通話セッションはありませんでした。メンバーリストの更新はスキップします。")
