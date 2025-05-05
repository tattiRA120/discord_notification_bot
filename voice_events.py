import discord
import datetime
import asyncio

import utils
from database import get_total_call_time, get_guild_settings, update_member_monthly_stats, record_voice_session_to_db

# 一人以下の状態になった通話チャンネルとその時刻、メンバー、関連タスクを記録する辞書
# キー: (guild_id, voice_channel_id), 値: {"start_time": datetimeオブジェクト, "member_id": int, "task": asyncio.Task}
lonely_voice_channels = {}

# 寝落ち確認メッセージとそれに対するリアクション監視タスクを記録する辞書
# キー: message_id, 値: {"member_id": int, "task": asyncio.Task}
sleep_check_messages = {}

# ボットがサーバーミュートしたメンバーのIDを記録するリスト
bot_muted_members = []

# --- 寝落ち確認とミュート処理 ---
async def check_lonely_channel(guild_id: int, channel_id: int, member_id: int):
    await asyncio.sleep(await get_lonely_timeout_seconds(guild_id)) # 設定された時間待機

    # 再度チャンネルの状態を確認
    guild = utils.bot.get_guild(guild_id)
    if not guild:
        return
    channel = guild.get_channel(channel_id)
    if not channel or len(channel.members) > 1 or (len(channel.members) == 1 and channel.members[0].id != member_id):
        # チャンネルが存在しない、複数人になった、または別の人が一人になった場合は処理しない
        key = (guild_id, channel_id)
        if key in lonely_voice_channels:
             lonely_voice_channels.pop(key) # 状態管理から削除
        return

    # まだ一人以下の状態の場合、寝落ち確認メッセージを送信
    if str(guild_id) in utils.server_notification_channels:
        notification_channel_id = utils.server_notification_channels[str(guild_id)]
        notification_channel = utils.bot.get_channel(notification_channel_id)
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
                    reaction_task = asyncio.create_task(wait_for_reaction(message.id, member_id, guild_id, channel_id))
                    sleep_check_messages[message.id] = {"member_id": member_id, "task": reaction_task}

                except discord.Forbidden:
                    print(f"エラー: チャンネル {notification_channel.name} ({notification_channel_id}) への送信権限がありません。")
                except Exception as e:
                    print(f"寝落ち確認メッセージ送信中にエラーが発生しました: {e}")
            else:
                 # メンバーが見つからない場合も状態管理から削除
                 key = (guild_id, channel_id)
                 if key in lonely_voice_channels:
                    lonely_voice_channels.pop(key)
        else:
            print(f"通知チャンネルが見つかりません: ギルドID {guild_id}")
            # 通知チャンネルがない場合も状態管理から削除
            key = (guild_id, channel_id)
            if key in lonely_voice_channels:
                lonely_voice_channels.pop(key)
    else:
        print(f"ギルド {guild.name} ({guild_id}) の通知チャンネルが設定されていません。寝落ち確認メッセージを送信できません。")
        # 通知チャンネルが設定されていない場合も状態管理から削除
        key = (guild_id, channel_id)
        if key in lonely_voice_channels:
            lonely_voice_channels.pop(key)


async def wait_for_reaction(message_id: int, member_id: int, guild_id: int, channel_id: int):
    settings = await get_guild_settings(guild_id)
    wait_seconds = settings["reaction_wait_minutes"] * 60

    try:
        # 指定された絵文字、ユーザーからのリアクションを待つ
        def check(reaction, user):
            return user.id == member_id and str(reaction.emoji) == '✅' and reaction.message.id == message_id

        await utils.bot.wait_for('reaction_add', timeout=wait_seconds, check=check)
        print(f"メンバー {member_id} がメッセージ {message_id} に反応しました。ミュート処理をキャンセルします。")
        guild = utils.bot.get_guild(guild_id)
        if guild and str(guild_id) in utils.server_notification_channels:
            notification_channel = utils.bot.get_channel(utils.server_notification_channels[str(guild_id)])
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
        guild = utils.bot.get_guild(guild_id)
        if guild:
            member = guild.get_member(member_id)
            if member:
                try:
                    await member.edit(mute=True, deafen=True)
                    print(f"メンバー {member.display_name} ({member_id}) をミュートしました。")
                    # ボットがミュートしたメンバーを記録
                    if member.id not in bot_muted_members:
                        bot_muted_members.append(member.id)

                    if str(guild_id) in utils.server_notification_channels:
                        notification_channel = utils.bot.get_channel(utils.server_notification_channels[str(guild_id)])
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
        key = (guild_id, channel_id)
        if key in lonely_voice_channels:
             lonely_voice_channels.pop(key)


async def get_lonely_timeout_seconds(guild_id):
    settings = await get_guild_settings(guild_id)
    return settings["lonely_timeout_minutes"] * 60 # 分を秒に変換

# --- イベントハンドラ ---
# @utils.bot.event # デコレータを削除
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
        if guild_id not in utils.call_sessions:
            utils.call_sessions[guild_id] = {}
        if voice_channel_id not in utils.call_sessions[guild_id]:
            start_time = now
            utils.call_sessions[guild_id][voice_channel_id] = {"start_time": start_time, "first_member": member.id}
            jst_time = utils.convert_utc_to_jst(start_time)
            embed = discord.Embed(title="通話開始", color=0xE74C3C)
            embed.set_thumbnail(url=f"{member.avatar.url}?size=128")
            embed.add_field(name="チャンネル", value=f"{after.channel.name}")
            embed.add_field(name="始めた人", value=f"{member.display_name}")
            embed.add_field(name="開始時間", value=f"{jst_time.strftime('%Y/%m/%d %H:%M:%S')}")
            if str(guild_id) in utils.server_notification_channels:
                notification_channel = utils.bot.get_channel(utils.server_notification_channels[str(guild_id)])
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

                    if str(m.guild.id) in utils.server_notification_channels:
                        notification_channel = utils.bot.get_channel(utils.server_notification_channels[str(m.guild.id)])
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

    elif before.channel is not None and after.channel is None:
        voice_channel_id = before.channel.id
        if guild_id in utils.call_sessions and voice_channel_id in utils.call_sessions[guild_id]:
            voice_channel = before.channel
            if len(voice_channel.members) == 0:
                session = utils.call_sessions[guild_id].pop(voice_channel_id)
                start_time = session["start_time"]
                call_duration = (now - start_time).total_seconds()
                duration_str = utils.format_duration(call_duration)
                embed = discord.Embed(title="通話終了", color=0x5865F2)
                embed.add_field(name="チャンネル", value=f"{voice_channel.name}")
                embed.add_field(name="通話時間", value=f"{duration_str}")
                if str(guild_id) in utils.server_notification_channels:
                    notification_channel = utils.bot.get_channel(utils.server_notification_channels[str(guild_id)])
                    if notification_channel:
                        await notification_channel.send(embed=embed)
                    else:
                        print(f"通知チャンネルが見つかりません: ギルドID {guild_id}")

    # --- 2人以上通話状態の記録（各メンバーごとに個別記録＋全参加者リストを維持する処理） ---

    # 退室処理（before.channel から退出した場合）
    if channel_before is not None:
        key = (guild_id, channel_before.id)
        if key in utils.active_voice_sessions:
            session_data = utils.active_voice_sessions[key]
            # もし対象メンバーが在室中ならその個人分の退室処理を実施
            if member.id in session_data["current_members"]:
                join_time = session_data["current_members"].pop(member.id)
                duration = (now - join_time).total_seconds()

                # --- 10時間達成チェック ---
                before_total = await get_total_call_time(member.id)
                month_key = join_time.strftime("%Y-%m")
                await update_member_monthly_stats(month_key, member.id, duration)
                after_total = await get_total_call_time(member.id)
                await utils.check_and_notify_milestone(member, member.guild, before_total, after_total)
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
                        await utils.check_and_notify_milestone(m_obj, member.guild, before_total_sess_end, after_total_sess_end)
                    else:
                         month_key = join_time.strftime("%Y-%m")
                         await update_member_monthly_stats(month_key, m_id, d)
                    # --- ここまで ---

                    session_data["current_members"].pop(m_id)

                overall_duration = (now - session_data["session_start"]).total_seconds()
                await record_voice_session_to_db(session_data["session_start"], overall_duration, list(session_data["all_participants"]))
                utils.active_voice_sessions.pop(key, None)

                # チャンネルの人数が1人以下になったら active_status_channels から削除
                if channel_before is not None and len(channel_before.members) < 2:
                    utils.active_status_channels.discard(key)
                    # 2人以上の通話がすべて終了した場合、ステータス更新タスクを停止しステータスをクリア
                    if not utils.active_status_channels and utils.update_call_status_task.is_running():
                        utils.update_call_status_task.stop()
                        await utils.bot.change_presence(activity=None)


    # 入室処理（after.channelに入室した場合）
    if channel_after is not None:
        key = (guild_id, channel_after.id)
        # チャンネル内の人数が2人以上の場合
        if len(channel_after.members) >= 2:
            if key not in utils.active_voice_sessions:
                # セッション開始時刻は、通話が2人以上になった時刻（この時点の now）
                utils.active_voice_sessions[key] = {
                    "session_start": now,
                    "current_members": { m.id: now for m in channel_after.members },
                    "all_participants": set(m.id for m in channel_after.members)
                }
            else:
                # 既存のセッションがある場合、新たに入室したメンバーを更新する
                session_data = utils.active_voice_sessions[key]
                for m in channel_after.members:
                    if m.id not in session_data["current_members"]:
                        session_data["current_members"][m.id] = now
                    session_data["all_participants"].add(m.id)

            # チャンネルの人数が2人以上になったら active_status_channels に追加
            if key not in utils.active_status_channels:
                utils.active_status_channels.add(key)
                # 初めて2人以上の通話が始まった場合、ステータス更新タスクを開始
                if not utils.update_call_status_task.is_running():
                    utils.update_call_status_task.start()

        else:
            # 人数が2人未満の場合は、既にセッションが存在する場合のみ更新する
            if key in utils.active_voice_sessions:
                session_data = utils.active_voice_sessions[key]
                for m in channel_after.members:
                    if m.id not in session_data["current_members"]:
                        session_data["current_members"][m.id] = now
                    session_data["all_participants"].add(m.id)
