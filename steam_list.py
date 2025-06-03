import time

async def handle_steam_list(self, event):
    '''列出所有玩家当前状态'''
    start_time = time.time()
    msg_lines = []
    now = int(time.time())
    for idx, sid in enumerate(self.STEAM_IDS):
        status = await self.fetch_player_status(sid, retry=1)
        if not status:
            msg_lines.append(f"❌ [{sid}] 获取失败")
        else:
            name = status.get('name') or sid
            gameid = status.get('gameid')
            game = status.get('gameextrainfo')
            lastlogoff = status.get('lastlogoff')
            personastate = status.get('personastate', 0)
            zh_game_name = await self.get_chinese_game_name(gameid, game) if gameid else (game or "未知游戏")
            if gameid:
                if sid not in self.start_play_times:
                    self.start_play_times[sid] = now
                play_seconds = now - self.start_play_times[sid]
                play_minutes = play_seconds / 60
                if play_minutes < 60:
                    play_str = f"{play_minutes:.1f}分钟"
                else:
                    play_str = f"{play_minutes/60:.1f}小时"
                msg = f"🟢 {name} 正在玩\n{zh_game_name} 已玩{play_str}"
                msg_lines.append(msg)
            elif personastate and int(personastate) > 0:
                msg_lines.append(f"🟡 {name} 在线")
            elif lastlogoff:
                hours_ago = (now - int(lastlogoff)) / 3600
                msg_lines.append(f"⚪️ {name} 离线\n上次在线 {hours_ago:.1f} 小时前")
            else:
                msg_lines.append(f"⚪️ {name} 离线")
        # 每位玩家后都加一个空行
        msg_lines.append("")
    elapsed = time.time() - start_time
    output = "\n".join(msg_lines)
    output += f"[{elapsed:.2f} 秒]"
    yield event.plain_result(output)
