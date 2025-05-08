import discord
import datetime
import asyncio
from discord.ext import commands # Cog を使用するためにインポート

from database import get_total_call_time, get_guild_settings, update_member_monthly_stats, record_voice_session_to_db
import config # config モジュールをインポート
import voice_state_manager # voice_state_manager モジュールをインポート
import formatters # formatters モジュールをインポート
import utils # check_and_notify_milestone のために utils をインポート

class SleepCheckManager:
    def __init__(self, bot):
        self.bot = bot
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
            print(f"メンバー {member_id} をbot_muted_membersに追加しました。")

    # bot_muted_members からメンバーを削除するヘルパー関数
    def remove_bot_muted_member(self, member_id: int):
        if member_id in self.bot_muted_members:
            self.bot_muted_members.remove(member_id)
            print(f"メンバー {member_id} をbot_muted_membersから削除しました。")

    # lonely_voice_channels にチャンネルを追加するヘルパー関数
    def add_lonely_channel(self, guild_id: int, channel_id: int, member_id: int, task: asyncio.Task):
        key = (guild_id, channel_id)
        self.lonely_voice_channels[key] = {
            "start_time": datetime.datetime.now(datetime.timezone.utc),
            "member_id": member_id,
            "task": task
        }
        print(f"チャンネル {channel_id} ({guild_id}) が一人以下になりました。メンバー: {member_id}")

    # lonely_voice_channels からチャンネルを削除するヘルパー関数
    def remove_lonely_channel(self, guild_id: int, channel_id: int, cancel_task: bool = True):
        key = (guild_id, channel_id)
        if key in self.lonely_voice_channels:
            if cancel_task and self.lonely_voice_channels[key]["task"] and not self.lonely_voice_channels[key]["task"].cancelled():
                self.lonely_voice_channels[key]["task"].cancel()
            self.lonely_voice_channels.pop(key)
            print(f"チャンネル {channel_id} ({guild_id}) の一人以下の状態を解除しました。")

    # --- 寝落ち確認とミュート処理 ---
    async def check_lonely_channel(self, guild_id: int, channel_id: int, member_id: int):
        await asyncio.sleep(await get_lonely_timeout_seconds(guild_id)) # 設定された時間待機

        # 再度チャンネルの状態を確認
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return
        channel = guild.get_channel(channel_id)
        # チャンネルが存在しない、またはタイムアウトしたメンバーがチャンネルにいない場合は処理しない
        if not channel or member_id not in [m.id for m in channel.members]:
            self.remove_lonely_channel(guild_id, channel_id, cancel_task=False) # タスク自体は完了しているのでキャンセルは不要
            return

        # チャンネルに一人だけ残っている、または複数人だが最初に一人になったメンバーがまだいる場合
        # 寝落ち確認メッセージを送信
        notification_channel_id = config.get_notification_channel_id(guild_id) # config から取得
        if notification_channel_id:
            notification_channel = self.bot.get_channel(notification_channel_id)
            if notification_channel:
                lonely_member = guild.get_member(member_id)
                if lonely_member:
                    embed = discord.Embed(
                        title="寝落ちミュート",
                        description=f"{lonely_member.mention} さん、{channel.name} chで一人になってから時間が経ちました。\n寝落ちしていませんか？反応がない場合、自動でサーバーミュートします。\nミュートをキャンセルする場合は、 :white_check_mark: を押してください。",
                        color=discord.Color.orange()
                    )
                    try:
                        message = await notification_channel.send(embed=embed)
                        await message.add_reaction("✅") # :white_check_mark: 絵文字を追加

                        # リアクション監視タスクを開始
                        reaction_task = asyncio.create_task(self.wait_for_reaction(message.id, member_id, guild_id, channel_id))
                        self.sleep_check_messages[message.id] = {"member_id": member_id, "task": reaction_task}

                    except discord.Forbidden:
                        print(f"エラー: チャンネル {notification_channel.name} ({notification_channel_id}) への送信権限がありません。")
                    except Exception as e:
                        print(f"寝落ち確認メッセージ送信中にエラーが発生しました: {e}")
                else:
                     # メンバーが見つからない場合も状態管理から削除
                     key = (guild_id, channel_id)
                     if key in self.lonely_voice_channels:
                        self.lonely_voice_channels.pop(key)
            else:
                print(f"通知チャンネルが見つかりません: ギルドID {guild_id}")
                # 通知チャンネルがない場合も状態管理から削除
                key = (guild_id, channel_id)
                if key in self.lonely_voice_channels:
                    self.lonely_voice_channels.pop(key)
        else:
            print(f"ギルド {guild.name} ({guild_id}) の通知チャンネルが設定されていません。寝落ち確認メッセージを送信できません。")
            # 通知チャンネルが設定されていない場合も状態管理から削除
            key = (guild_id, channel_id)
            if key in self.lonely_voice_channels:
                self.lonely_voice_channels.pop(key)


    async def wait_for_reaction(self, message_id: int, member_id: int, guild_id: int, channel_id: int):
        settings = await get_guild_settings(guild_id)
        wait_seconds = settings["reaction_wait_minutes"] * 60

        try:
            # 指定された絵文字、ユーザーからのリアクションを待つ
            def check(reaction, user):
                return user.id == member_id and str(reaction.emoji) == '✅' and reaction.message.id == message_id

            await self.bot.wait_for('reaction_add', timeout=wait_seconds, check=check)
            print(f"メンバー {member_id} がメッセージ {message_id} に反応しました。ミュート処理をキャンセルします。")
            guild = self.bot.get_guild(guild_id)
            notification_channel_id = config.get_notification_channel_id(guild_id) # config から取得
            if guild and notification_channel_id:
                notification_channel = self.bot.get_channel(notification_channel_id)
                if notification_channel:
                    try:
                        member = guild.get_member(member_id)
                        if member:
                            embed = discord.Embed(title="寝落ちミュート", description=f"{member.mention} さんが反応しました。\nサーバーミュートをキャンセルしました。", color=discord.Color.green())
                            await notification_channel.send(embed=embed)
                    except discord.Forbidden:
                        print(f"エラー: チャンネル {notification_channel.name} ({notification_channel.id}) への送信権限がありません。")
                    except Exception as e:
                        print(f"ミュートキャンセルメッセージ送信中にエラーが発生しました: {e}")


        except asyncio.TimeoutError:
            # タイムアウトした場合、ミュート処理を実行
            print(f"メッセージ {message_id} への反応がありませんでした。メンバー {member_id} をミュートします。")
            guild = self.bot.get_guild(guild_id)
            if guild:
                member = guild.get_member(member_id)
                if member:
                    try:
                        await member.edit(mute=True, deafen=False)
                        print(f"メンバー {member.display_name} ({member.id}) をミュートしました。")
                        # ボットがミュートしたメンバーを記録
                        self.add_bot_muted_member(member.id)

                        notification_channel_id = config.get_notification_channel_id(guild_id) # config から取得
                        if notification_channel_id:
                            notification_channel = self.bot.get_channel(notification_channel_id)
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
            if message_id in self.sleep_check_messages:
                self.sleep_check_messages.pop(message_id)
            # チャンネルの状態管理からも削除（ミュートされたか反応があったかで一人以下の状態は終了とみなす）
            key = (guild_id, channel_id)
            if key in self.lonely_voice_channels:
                 self.lonely_voice_channels.pop(key)


async def get_lonely_timeout_seconds(guild_id):
    settings = await get_guild_settings(guild_id)
    return settings["lonely_timeout_minutes"] * 60 # 分を秒に変換

# --- イベントハンドラ ---

class VoiceEvents(commands.Cog):
    def __init__(self, bot, sleep_check_manager: SleepCheckManager, voice_state_manager: voice_state_manager.VoiceStateManager):
        self.bot = bot
        self.sleep_check_manager = sleep_check_manager
        self.voice_state_manager = voice_state_manager

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        guild_id = member.guild.id
        now = datetime.datetime.now(datetime.timezone.utc)

        channel_before = before.channel
        channel_after = after.channel

        # SleepCheckManager のロジックはここに残す
        key_before = (guild_id, channel_before.id) if channel_before else None
        key_after = (guild_id, channel_after.id) if channel_after else None

        # チャンネルから退出した場合
        if channel_before is not None and channel_after is None:
            # 退室したメンバーに関連付けられた寝落ち確認タスクがあればキャンセル
            message_ids_to_remove = []
            for message_id, data in self.sleep_check_manager.sleep_check_messages.items():
                if data["member_id"] == member.id:
                    if data["task"] and not data["task"].cancelled():
                        data["task"].cancel()
                        print(f"メンバー {member.id} の退出により、メッセージ {message_id} のリアクション監視タスクをキャンセルしました。")
                    message_ids_to_remove.append(message_id)

            for message_id in message_ids_to_remove:
                self.sleep_check_manager.sleep_check_messages.pop(message_id)
                print(f"メッセージ {message_id} をsleep_check_messagesから削除しました。")

            # 退室したチャンネルに誰もいなくなった場合、一人以下の状態を解除
            if channel_before is not None and len(channel_before.members) == 0:
                self.sleep_check_manager.remove_lonely_channel(guild_id, channel_before.id)
            # 退室したチャンネルに一人だけ残った場合、そのメンバーに対して一人以下の状態を開始
            elif channel_before is not None and len(channel_before.members) == 1:
                lonely_member = channel_before.members[0]
                if key_before not in self.sleep_check_manager.lonely_voice_channels and lonely_member.id not in self.sleep_check_manager.bot_muted_members:
                    task = asyncio.create_task(self.sleep_check_manager.check_lonely_channel(guild_id, channel_before.id, lonely_member.id))
                    self.sleep_check_manager.add_lonely_channel(guild_id, channel_before.id, lonely_member.id, task)

            # VoiceStateManager に処理を委譲し、統計更新が必要なデータを取得
            ended_sessions_data = await self.voice_state_manager.handle_member_leave(member, channel_before)
            # VoiceStateManager から統計更新が必要なデータが返された場合、各メンバーごとに処理
            for member_id, duration, join_time in ended_sessions_data:
                before_total = await get_total_call_time(member_id)
                month_key = join_time.strftime("%Y-%m")
                await update_member_monthly_stats(month_key, member_id, duration)
                after_total = await get_total_call_time(member_id)
                m_obj = member.guild.get_member(member_id) if member.guild else None
                if m_obj:
                    await utils.check_and_notify_milestone(self.bot, m_obj, member.guild, before_total, after_total)


        # チャンネルに入室した場合
        elif channel_before is None and channel_after is not None:
            # 入室したチャンネルが一人以下になった場合、そのメンバーに対して一人以下の状態を開始
            if len(channel_after.members) == 1:
                lonely_member = channel_after.members[0]
                if key_after not in self.sleep_check_manager.lonely_voice_channels and lonely_member.id not in self.sleep_check_manager.bot_muted_members:
                    task = asyncio.create_task(self.sleep_check_manager.check_lonely_channel(guild_id, channel_after.id, lonely_member.id))
                    self.sleep_check_manager.add_lonely_channel(guild_id, channel_after.id, lonely_member.id, task)
            # 入室したチャンネルが複数人になった場合、一人以下の状態を解除
            elif len(channel_after.members) > 1:
                self.sleep_check_manager.remove_lonely_channel(guild_id, channel_after.id)

            # VoiceStateManager に処理を委譲
            await self.voice_state_manager.handle_member_join(member, channel_after)

            # ボットによってミュートされたメンバーが再入室した場合、ミュートを解除
            if member.id in self.sleep_check_manager.bot_muted_members:
                async def unmute_after_delay(m: discord.Member):
                    await asyncio.sleep(1) # 1秒待機
                    try:
                        await m.edit(mute=False, deafen=False)
                        self.sleep_check_manager.remove_bot_muted_member(m.id)
                        print(f"メンバー {m.display_name} ({m.id}) が再入室したためミュートを解除しました。")

                        notification_channel_id = config.get_notification_channel_id(m.guild.id)
                        if notification_channel_id:
                            notification_channel = self.bot.get_channel(notification_channel_id)
                            if notification_channel:
                                try:
                                    embed = discord.Embed(title="寝落ちミュート", description=f"{m.mention} さんが再入室したため、サーバーミュートを解除しました。", color=discord.Color.green())
                                    await notification_channel.send(embed=embed)
                                except discord.Forbidden:
                                    print(f"エラー: チャンネル {notification_channel.name} ({notification_channel.id}) への送信権限がありません。")
                                except Exception as e:
                                    print(f"再入室時ミュート解除メッセージ送信中にエラーが発生しました: {e}")

                    except discord.Forbidden:
                        print(f"エラー: メンバー {m.display_name} ({m.id}) のミュートを解除する権限がありません。")
                    except Exception as e:
                        print(f"メンバーミュート解除中にエラーが発生しました: {e}")

                asyncio.create_task(unmute_after_delay(member))


        # チャンネル間を移動した場合
        elif channel_before is not None and channel_after is not None and channel_before != channel_after:
            # 移動元チャンネルに誰もいなくなった場合、一人以下の状態を解除
            if len(channel_before.members) == 0:
                self.sleep_check_manager.remove_lonely_channel(guild_id, channel_before.id)
            # 移動元チャンネルに一人だけ残った場合、そのメンバーに対して一人以下の状態を開始
            elif len(channel_before.members) == 1:
                lonely_member = channel_before.members[0]
                if key_before not in self.sleep_check_manager.lonely_voice_channels and lonely_member.id not in self.sleep_check_manager.bot_muted_members:
                    task = asyncio.create_task(self.sleep_check_manager.check_lonely_channel(guild_id, channel_before.id, lonely_member.id))
                    self.sleep_check_manager.add_lonely_channel(guild_id, channel_before.id, lonely_member.id, task)

            # 移動先チャンネルが一人以下になった場合、そのメンバーに対して一人以下の状態を開始
            if len(channel_after.members) == 1:
                lonely_member = channel_after.members[0]
                if key_after not in self.sleep_check_manager.lonely_voice_channels and lonely_member.id not in self.sleep_check_manager.bot_muted_members:
                    task = asyncio.create_task(self.sleep_check_manager.check_lonely_channel(guild_id, channel_after.id, lonely_member.id))
                    self.sleep_check_manager.add_lonely_channel(guild_id, channel_after.id, lonely_member.id, task)
            # 移動先チャンネルが複数人になった場合、一人以下の状態を解除
            elif len(channel_after.members) > 1:
                self.sleep_check_manager.remove_lonely_channel(guild_id, channel_after.id)

            # VoiceStateManager に処理を委譲し、統計更新が必要なデータを取得
            ended_sessions_from_before, joined_session_data = await self.voice_state_manager.handle_member_move(member, channel_before, channel_after)

            # 移動元での退出による統計更新とマイルストーン通知
            for member_id_leave, duration_leave, join_time_leave in ended_sessions_from_before:
                before_total_leave = await get_total_call_time(member_id_leave)
                month_key_leave = join_time_leave.strftime("%Y-%m")
                await update_member_monthly_stats(month_key_leave, member_id_leave, duration_leave)
                after_total_leave = await get_total_call_time(member_id_leave)
                m_obj_leave = member.guild.get_member(member_id_leave) if member.guild else None
                if m_obj_leave:
                    await utils.check_and_notify_milestone(self.bot, m_obj_leave, member.guild, before_total_leave, after_total_leave)

            # 移動先での入室による統計更新とマイルストーン通知 (移動してきたメンバー自身の場合のみ)
            if joined_session_data is not None:
                 member_id_join, duration_join, join_time_join = joined_session_data
                 before_total_join = await get_total_call_time(member_id_join)
                 month_key_join = join_time_join.strftime("%Y-%m")
                 await update_member_monthly_stats(month_key_join, member_id_join, 0) # 移動直後は通話時間0として記録
                 after_total_join = await get_total_call_time(member_id_join)
                 m_obj_join = member.guild.get_member(member_id_join) if member.guild else None
                 if m_obj_join:
                     await utils.check_and_notify_milestone(self.bot, m_obj_join, member.guild, before_total_join, after_total_join)


            # ボットによってミュートされたメンバーがチャンネル移動した場合、ミュートを解除
            if member.id in self.sleep_check_manager.bot_muted_members:
                async def unmute_after_delay(m: discord.Member):
                    await asyncio.sleep(1) # 1秒待機
                    try:
                        await m.edit(mute=False, deafen=False)
                        self.sleep_check_manager.remove_bot_muted_member(m.id)
                        print(f"メンバー {m.display_name} ({m.id}) がチャンネル移動したためミュートを解除しました。")

                        notification_channel_id = config.get_notification_channel_id(m.guild.id)
                        if notification_channel_id:
                            notification_channel = self.bot.get_channel(notification_channel_id)
                            if notification_channel:
                                try:
                                    embed = discord.Embed(title="寝落ちミュート", description=f"{m.mention} さんがチャンネル移動したため、サーバーミュートを解除しました。", color=discord.Color.green())
                                    await notification_channel.send(embed=embed)
                                except discord.Forbidden:
                                    print(f"エラー: チャンネル {notification_channel.name} ({notification_channel.id}) への送信権限がありません。")
                                except Exception as e:
                                    print(f"チャンネル移動時ミュート解除メッセージ送信中にエラーが発生しました: {e}")

                    except discord.Forbidden:
                        print(f"エラー: メンバー {m.display_name} ({m.id}) のミュートを解除する権限がありません。")
                    except Exception as e:
                        print(f"メンバーミュート解除中にエラーが発生しました: {e}")

                asyncio.create_task(unmute_after_delay(member))


        # 同一チャンネル内での状態変化の場合は何もしない
        elif channel_before == channel_after:
            pass # 既存の処理を維持するため pass を使用

        # 通話終了処理 (before.channel から完全に退出した場合)
        elif before.channel is not None and after.channel is None:
             # VoiceStateManager に処理を委譲し、統計更新が必要なデータを取得
            ended_sessions_data = await self.voice_state_manager.handle_member_leave(member, before.channel)
            # VoiceStateManager から統計更新が必要なデータが返された場合、各メンバーごとに処理
            for member_id, duration, join_time in ended_sessions_data:
                before_total = await get_total_call_time(member_id)
                month_key = join_time.strftime("%Y-%m")
                await update_member_monthly_stats(month_key, member_id, duration)
                after_total = await get_total_call_time(member_id)
                m_obj = member.guild.get_member(member_id) if member.guild else None
                if m_obj:
                    await utils.check_and_notify_milestone(self.bot, m_obj, member.guild, before_total, after_total)

        # 通話開始処理 (before.channel が None で after.channel が None でない場合)
        elif before.channel is None and after.channel is not None:
             # VoiceStateManager に処理を委譲
             await self.voice_state_manager.handle_member_join(member, after.channel)

             # ボットによってミュートされたメンバーが再入室した場合、ミュートを解除
             if member.id in self.sleep_check_manager.bot_muted_members:
                 async def unmute_after_delay(m: discord.Member):
                     await asyncio.sleep(1) # 1秒待機
                     try:
                         await m.edit(mute=False, deafen=False)
                         self.sleep_check_manager.remove_bot_muted_member(m.id)
                         print(f"メンバー {m.display_name} ({m.id}) が再入室したためミュートを解除しました。")

                         notification_channel_id = config.get_notification_channel_id(m.guild.id)
                         if notification_channel_id:
                             notification_channel = self.bot.get_channel(notification_channel_id)
                             if notification_channel:
                                 try:
                                     embed = discord.Embed(title="寝落ちミュート", description=f"{m.mention} さんが再入室したため、サーバーミュートを解除しました。", color=discord.Color.green())
                                     await notification_channel.send(embed=embed)
                                 except discord.Forbidden:
                                     print(f"エラー: チャンネル {notification_channel.name} ({notification_channel.id}) への送信権限がありません。")
                                 except Exception as e:
                                     print(f"再入室時ミュート解除メッセージ送信中にエラーが発生しました: {e}")

                     except discord.Forbidden:
                         print(f"エラー: メンバー {m.display_name} ({m.id}) のミュートを解除する権限がありません。")
                     except Exception as e:
                         print(f"メンバーミュート解除中にエラーが発生しました: {e}")

                 asyncio.create_task(unmute_after_delay(member))

        # --- 2人以上通話状態の記録（各メンバーごとに個別記録＋全参加者リストを維持する処理） ---
        # この部分は VoiceStateManager に移動したので削除
        pass
