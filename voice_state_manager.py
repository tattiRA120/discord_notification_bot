import discord
from discord.ext import tasks
import datetime
from zoneinfo import ZoneInfo

from database import record_voice_session_to_db
from formatters import format_duration, convert_utc_to_jst
from config import get_notification_channel_id


class VoiceStateManager:
    def __init__(self, bot):
        self.bot = bot
        # 通話開始時間と最初に通話を開始した人を記録する辞書（通話通知用）
        # キー: (guild_id, voice_channel_id), 値: {"start_time": datetimeオブジェクト, "first_member": member_id}
        self.call_sessions = {}

        # (guild_id, channel_id) をキーに、現在進行中の「2人以上通話セッション」を記録する
        # 値: {"session_start": datetimeオブジェクト, "current_members": {member_id: join_time}, "all_participants": {member_id}}
        self.active_voice_sessions = {}

        # 2人以上が通話中のチャンネルを追跡するセット
        # 要素: (guild_id, channel_id)
        self.active_status_channels = set()

        # ステータス通話時間更新タスク
        self.update_call_status_task = tasks.loop(seconds=15)(self._update_call_status_task)

    # --- ボイスステート更新ハンドラ ---
    async def handle_member_join(self, member: discord.Member, channel_after: discord.VoiceChannel):
        """メンバーがチャンネルに参加した時の処理"""
        guild_id = member.guild.id
        now = datetime.datetime.now(datetime.timezone.utc)
        key = (guild_id, channel_after.id)

        # 通話通知機能 (入室時)
        if guild_id not in self.call_sessions:
            self.call_sessions[guild_id] = {}
        if channel_after.id not in self.call_sessions[guild_id]:
            start_time = now
            self.call_sessions[guild_id][channel_after.id] = {"start_time": start_time, "first_member": member.id}
            jst_time = convert_utc_to_jst(start_time)
            embed = discord.Embed(title="通話開始", color=discord.Color.red())
            embed.set_thumbnail(url=f"{member.avatar.url}?size=128")
            embed.add_field(name="チャンネル", value=f"{channel_after.name}")
            embed.add_field(name="始めた人", value=f"{member.display_name}")
            embed.add_field(name="開始時間", value=f"{jst_time.strftime('%Y/%m/%d %H:%M:%S')}")
            notification_channel_id = get_notification_channel_id(guild_id)
            if notification_channel_id:
                notification_channel = self.bot.get_channel(notification_channel_id)
                if notification_channel:
                    await notification_channel.send(content="@everyone", embed=embed, allowed_mentions=discord.AllowedMentions(everyone=True))
                else:
                    print(f"通知チャンネルが見つかりません:ギルドID {guild_id}")

        # 2人以上通話状態の記録（各メンバーごとに個別記録＋全参加者リストを維持する処理）
        # チャンネル内の人数が2人以上の場合
        if len(channel_after.members) >= 2:
            if key not in self.active_voice_sessions:
                # セッション開始時刻は、通話が2人以上になった時刻（この時点の now）
                self.active_voice_sessions[key] = {
                    "session_start": now,
                    "current_members": { m.id: now for m in channel_after.members },
                    "all_participants": set(m.id for m in channel_after.members)
                }
            else:
                # 既存のセッションがある場合、新たに入室したメンバーを更新する
                session_data = self.active_voice_sessions[key]
                for m in channel_after.members:
                    if m.id not in session_data["current_members"]:
                        session_data["current_members"][m.id] = now
                    session_data["all_participants"].add(m.id)

            # チャンネルの人数が2人以上になったら active_status_channels に追加
            if key not in self.active_status_channels:
                self.active_status_channels.add(key)
                # 初めて2人以上の通話が始まった場合、ステータス更新タスクを開始
                if not self.update_call_status_task.is_running():
                    self.update_call_status_task.start()

        else:
            # 人数が2人未満の場合は、既にセッションが存在する場合のみ更新する
            if key in self.active_voice_sessions:
                session_data = self.active_voice_sessions[key]
                for m in channel_after.members:
                    if m.id not in session_data["current_members"]:
                        session_data["current_members"][m.id] = now
                    session_data["all_participants"].add(m.id)

    async def handle_member_leave(self, member: discord.Member, channel_before: discord.VoiceChannel):
        """メンバーがチャンネルから退出した時の処理"""
        guild_id = member.guild.id
        now = datetime.datetime.now(datetime.timezone.utc)
        key = (guild_id, channel_before.id)

        # 通話通知機能 (退出時)
        voice_channel_before_id = channel_before.id
        if guild_id in self.call_sessions and voice_channel_before_id in self.call_sessions[guild_id]:
            voice_channel = channel_before
            # 退出元のチャンネルに誰もいなくなった場合のみ通話終了とみなす
            if len(voice_channel.members) == 0:
                session = self.call_sessions[guild_id].pop(voice_channel_before_id)
                start_time = session["start_time"]
                call_duration = (now - start_time).total_seconds()
                duration_str = format_duration(call_duration)
                embed = discord.Embed(title="通話終了", color=discord.Color.blurple())
                embed.add_field(name="チャンネル", value=f"{voice_channel.name}")
                embed.add_field(name="通話時間", value=f"{duration_str}")
                notification_channel_id = get_notification_channel_id(guild_id)
                if notification_channel_id:
                    notification_channel = self.bot.get_channel(notification_channel_id)
                    if notification_channel:
                        await notification_channel.send(embed=embed)
                    else:
                        print(f"通知チャンネルが見つかりません:ギルドID {guild_id}")

        # 2人以上通話状態の記録（各メンバーごとに個別記録＋全参加者リストを維持する処理）
        if key in self.active_voice_sessions:
            session_data = self.active_voice_sessions[key]
            ended_sessions_data = [] # Collect data for ended individual sessions

            # もし対象メンバーが在室中ならその個人分の退室処理を実施
            if member.id in session_data["current_members"]:
                join_time = session_data["current_members"].pop(member.id)
                duration = (now - join_time).total_seconds()
                ended_sessions_data.append((member.id, duration, join_time)) # Add to list

            # もし退室後、チャンネル内人数が1人以下ならセッション終了処理を実施
            if channel_before is not None and len(channel_before.members) < 2:
                # セッション終了時の残メンバーの統計更新と通知チェック
                remaining_members_data = session_data["current_members"].copy()
                for m_id, join_time in remaining_members_data.items():
                    d = (now - join_time).total_seconds()
                    ended_sessions_data.append((m_id, d, join_time)) # Add to list
                    session_data["current_members"].pop(m_id) # Remove from current members

                overall_duration = (now - session_data["session_start"]).total_seconds()
                await record_voice_session_to_db(session_data["session_start"], overall_duration, list(session_data["all_participants"]))
                self.active_voice_sessions.pop(key, None)

                # チャンネルの人数が1人以下になったら active_status_channels から削除
                if channel_before is not None and len(channel_before.members) < 2:
                    self.active_status_channels.discard(key)
                    # 2人以上の通話がすべて終了した場合、ステータス更新タスクを停止しステータスをクリア
                    if not self.active_status_channels and self.update_call_status_task.is_running():
                        self.update_call_status_task.stop()
                        await self.bot.change_presence(activity=None)

            return ended_sessions_data # Return the list of ended sessions data

        return [] # Return empty list if no active session or no sessions ended

    async def handle_member_move(self, member: discord.Member, channel_before: discord.VoiceChannel, channel_after: discord.VoiceChannel):
        """メンバーがチャンネル間を移動した時の処理"""
        guild_id = member.guild.id
        now = datetime.datetime.now(datetime.timezone.utc)
        key_before = (guild_id, channel_before.id)
        key_after = (guild_id, channel_after.id)

        # 移動元チャンネルからの退出処理 (通話通知用)
        voice_channel_before_id = channel_before.id
        if guild_id in self.call_sessions and voice_channel_before_id in self.call_sessions[guild_id]:
            voice_channel = channel_before
            # 移動元のチャンネルに誰もいなくなった場合のみ通話終了とみなす
            if len(voice_channel.members) == 0:
                session = self.call_sessions[guild_id].pop(voice_channel_before_id)
                start_time = session["start_time"]
                call_duration = (now - start_time).total_seconds()
                duration_str = format_duration(call_duration)
                embed = discord.Embed(title="通話終了", color=discord.Color.blurple())
                embed.add_field(name="チャンネル", value=f"{voice_channel.name}")
                embed.add_field(name="通話時間", value=f"{duration_str}")
                notification_channel_id = get_notification_channel_id(guild_id)
                if notification_channel_id:
                    notification_channel = self.bot.get_channel(notification_channel_id)
                    if notification_channel:
                        await notification_channel.send(embed=embed)
                    else:
                        print(f"通知チャンネルが見つかりません:ギルドID {guild_id}")

        # 移動先チャンネルへの入室処理 (通話通知用)
        voice_channel_after_id = channel_after.id
        if guild_id not in self.call_sessions:
            self.call_sessions[guild_id] = {}
        # 移動先のチャンネルに誰もいない状態から一人になった場合、または最初から一人で通話に参加した場合に通話開始とみなす
        if voice_channel_after_id not in self.call_sessions[guild_id] and len(channel_after.members) == 1:
             start_time = now
             self.call_sessions[guild_id][voice_channel_after_id] = {"start_time": start_time, "first_member": member.id}
             jst_time = convert_utc_to_jst(start_time)
             embed = discord.Embed(title="通話開始", color=discord.Color.red())
             embed.set_thumbnail(url=f"{member.avatar.url}?size=128")
             embed.add_field(name="チャンネル", value=f"{channel_after.name}")
             embed.add_field(name="始めた人", value=f"{member.display_name}")
             embed.add_field(name="開始時間", value=f"{jst_time.strftime('%Y/%m/%d %H:%M:%S')}")
             notification_channel_id = get_notification_channel_id(guild_id)
             if notification_channel_id:
                 notification_channel = self.bot.get_channel(notification_channel_id)
                 if notification_channel:
                     await notification_channel.send(content="@everyone", embed=embed, allowed_mentions=discord.AllowedMentions(everyone=True))
                 else:
                     print(f"通知チャンネルが見つかりません:ギルドID {guild_id}")

        # 2人以上通話状態の記録（各メンバーごとに個別記録＋全参加者リストを維持する処理）

        ended_sessions_from_before = [] # Collect data for sessions ending in channel_before
        joined_session_data = None # Data for the member joining channel_after

        # 移動元チャンネルからの退出処理
        if key_before in self.active_voice_sessions:
            session_data_before = self.active_voice_sessions[key_before]
            if member.id in session_data_before["current_members"]:
                join_time_leave = session_data_before["current_members"].pop(member.id)
                duration_leave = (now - join_time_leave).total_seconds()
                ended_sessions_from_before.append((member.id, duration_leave, join_time_leave)) # Add to list

            if len(channel_before.members) < 2:
                # セッション終了時の残メンバーの統計更新と通知チェック
                remaining_members_data = session_data_before["current_members"].copy()
                for m_id, join_time in remaining_members_data.items():
                    d = (now - join_time).total_seconds()
                    ended_sessions_from_before.append((m_id, d, join_time)) # Add to list
                    session_data_before["current_members"].pop(m_id) # Remove from current members

                overall_duration = (now - session_data_before["session_start"]).total_seconds()
                await record_voice_session_to_db(session_data_before["session_start"], overall_duration, list(session_data_before["all_participants"]))
                self.active_voice_sessions.pop(key_before, None)

                if len(channel_before.members) < 2:
                    self.active_status_channels.discard(key_before)
                    if not self.active_status_channels and self.update_call_status_task.is_running():
                        self.update_call_status_task.stop()
                        await self.bot.change_presence(activity=None)

        # 移動先チャンネルへの入室処理
        if len(channel_after.members) >= 2:
            if key_after not in self.active_voice_sessions:
                self.active_voice_sessions[key_after] = {
                    "session_start": now,
                    "current_members": { m.id: now for m in channel_after.members },
                    "all_participants": set(m.id for m in channel_after.members)
                }
            else:
                session_data_after = self.active_voice_sessions[key_after]
                for m in channel_after.members:
                    if m.id not in session_data_after["current_members"]:
                        session_data_after["current_members"][m.id] = now
                        if m.id == member.id: # 移動してきたメンバーの場合
                            # For the joining member, we only need their ID and join time for potential future calculation
                            # Duration is 0 at the moment of joining
                            joined_session_data = (m.id, 0, now)
                    session_data_after["all_participants"].add(m.id)

            if key_after not in self.active_status_channels:
                self.active_status_channels.add(key_after)
                if not self.update_call_status_task.is_running():
                    self.update_call_status_task.start()
        else:
            if key_after in self.active_voice_sessions:
                session_data_after = self.active_voice_sessions[key_after]
                for m in channel_after.members:
                    if m.id not in session_data_after["current_members"]:
                        session_data_after["current_members"][m.id] = now
                        if m.id == member.id: # 移動してきたメンバーの場合
                            joined_session_data = (m.id, 0, now)
                    session_data_after["all_participants"].add(m.id)


        # Return data for sessions that ended in channel_before and data for the member joining channel_after
        return ended_sessions_from_before, joined_session_data


    # --- 二人以上の通話時間計算ヘルパー関数 ---
    def calculate_call_duration_seconds(self, start_time):
        """開始時刻からの経過秒数を計算する"""
        now = datetime.datetime.now(datetime.timezone.utc)
        return (now - start_time).total_seconds()

    # アクティブな通話チャンネルとその通話時間を取得する
    def get_active_call_durations(self, guild_id: int):
        """指定されたギルドのアクティブな通話チャンネルとその通話時間を取得する"""
        active_calls = []
        now = datetime.datetime.now(datetime.timezone.utc)
        for key, session_data in self.active_voice_sessions.items():
            if key[0] == guild_id:
                channel = self.bot.get_channel(key[1])
                if channel and isinstance(channel, discord.VoiceChannel):
                    duration_seconds = (now - session_data["session_start"]).total_seconds()
                    formatted_duration = format_duration(duration_seconds)
                    active_calls.append({"channel_name": channel.name, "duration": formatted_duration})
        return active_calls

    # --- ステータス通話時間更新タスク ---
    async def _update_call_status_task(self):
        """ボットのステータスを通話時間で更新するタスク"""
        if self.active_status_channels:
            # active_status_channelsからステータスに表示するチャンネルを選択
            channel_key_to_display = next(iter(self.active_status_channels))
            guild_id, channel_id = channel_key_to_display
            guild = self.bot.get_guild(guild_id)
            channel = self.bot.get_channel(channel_id)

            if guild and channel and channel_key_to_display in self.active_voice_sessions:
                # 選択したチャンネルの通話時間を計算し、ステータスに設定
                session_data = self.active_voice_sessions[channel_key_to_display]
                duration_seconds = self.calculate_call_duration_seconds(session_data["session_start"])
                formatted_duration = format_duration(duration_seconds)
                activity = discord.CustomActivity(name=f"{channel.name}: {formatted_duration}")
                await self.bot.change_presence(activity=activity)
            else:
                # チャンネルが見つからない、またはactive_voice_sessionsにない場合はセットから削除しステータスをクリア
                self.active_status_channels.discard(channel_key_to_display)
                if not self.active_status_channels:
                     await self.bot.change_presence(activity=None)
        else:
            # 2人以上の通話がない場合はステータスをクリア
            await self.bot.change_presence(activity=None)

    async def record_session_end(self, guild_id: int, channel_id: int):
        """通話セッション終了時の処理（データベース記録など）"""
        key = (guild_id, channel_id)
        if key in self.active_voice_sessions:
            session_data = self.active_voice_sessions[key]
            now = datetime.datetime.now(datetime.timezone.utc)

            # セッション終了時の残メンバーの統計更新と通知チェック
            remaining_members_data = session_data["current_members"].copy()
            for m_id, join_time in remaining_members_data.items():
                d = (now - join_time).total_seconds()

                # 統計更新とマイルストーン通知は voice_events.py で行うため、ここではデータを返さない
                pass

                session_data["current_members"].pop(m_id)

            overall_duration = (now - session_data["session_start"]).total_seconds()
            await record_voice_session_to_db(session_data["session_start"], overall_duration, list(session_data["all_participants"]))
            self.active_voice_sessions.pop(key, None)

            # チャンネルの人数が1人以下になったら active_status_channels から削除
            self.active_status_channels.discard(key)
            # 2人以上の通話がすべて終了した場合、ステータス更新タスクを停止しステータスをクリア
            if not self.active_status_channels and self.update_call_status_task.is_running():
                self.update_call_status_task.stop()
                await self.bot.change_presence(activity=None)

    def start_session(self, guild_id: int, channel_id: int, members: list[discord.Member]):
        """新しい通話セッションを開始する"""
        key = (guild_id, channel_id)
        now = datetime.datetime.now(datetime.timezone.utc)
        self.active_voice_sessions[key] = {
            "session_start": now,
            "current_members": { m.id: now for m in members },
            "all_participants": set(m.id for m in members)
        }
        self.active_status_channels.add(key)
        if not self.update_call_status_task.is_running():
            self.update_call_status_task.start()

    def update_session_members(self, guild_id: int, channel_id: int, members: list[discord.Member]):
        """既存の通話セッションのメンバーリストを更新する"""
        key = (guild_id, channel_id)
        now = datetime.datetime.now(datetime.timezone.utc)
        if key in self.active_voice_sessions:
            session_data = self.active_voice_sessions[key]
            for m in members:
                if m.id not in session_data["current_members"]:
                    session_data["current_members"][m.id] = now
                session_data["all_participants"].add(m.id)
