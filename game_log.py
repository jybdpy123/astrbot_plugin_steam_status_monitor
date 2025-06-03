import os
import json
import asyncio
from datetime import datetime, timedelta

class GameLogManager:
    def __init__(self):
        self.log_path = os.path.join(os.path.dirname(__file__), "game_log.json")
        self._lock = asyncio.Lock()
        self._logs = None

    async def _load(self):
        if self._logs is not None:
            return
        if os.path.exists(self.log_path):
            try:
                with open(self.log_path, "r", encoding="utf-8") as f:
                    self._logs = json.load(f)
            except Exception:
                self._logs = []
        else:
            self._logs = []

    async def _save(self):
        async with self._lock:
            with open(self.log_path, "w", encoding="utf-8") as f:
                json.dump(self._logs, f, ensure_ascii=False, indent=2)

    async def record_log(self, steamid, player_name, gameid, game_name, duration, end_time):
        await self._load()
        log_item = {
            "steamid": steamid,
            "player_name": player_name,
            "gameid": gameid,
            "game_name": game_name,
            "duration": duration,
            "end_time": end_time
        }
        self._logs.append(log_item)
        await self._save()

    async def get_logs_24h(self, steam_ids):
        await self._load()
        now = int(datetime.now().timestamp())
        logs = []
        for item in self._logs:
            if item["steamid"] in steam_ids and now - item["end_time"] <= 86400:
                logs.append(item)
        return logs

    async def clear_logs_older_than(self, hours):
        '''清除指定小时数以外的日志，返回剩余日志条数'''
        await self._load()
        now = int(datetime.now().timestamp())
        cutoff = now - int(hours * 3600)
        self._logs = [item for item in self._logs if item.get("end_time", 0) >= cutoff]
        await self._save()
        return len(self._logs)

async def handle_steam_log(self, event):
    '''输出24小时内所有玩家的游玩记录，分玩家分组显示，无记录也列出玩家名。
    若日志和状态都无玩家名，则实时调用Steam API获取玩家名并缓存。'''
    logs = await self.game_log.get_logs_24h(self.STEAM_IDS)
    logs_by_user = {sid: [] for sid in self.STEAM_IDS}
    for item in logs:
        logs_by_user[item["steamid"]].append(item)
    # 优化：只查一次API，后续直接用self.last_states缓存
    name_map = {}
    for sid in self.STEAM_IDS:
        user_logs = logs_by_user[sid]
        player_name = None
        if user_logs:
            user_logs.sort(key=lambda x: x["end_time"], reverse=True)
            player_name = user_logs[0].get("player_name")
        if not player_name:
            state = self.last_states.get(sid)
            if state and state.get('name'):
                player_name = state.get('name')
            else:
                # 只在本地缓存没有时才查API
                if sid not in getattr(self, "_steam_log_api_checked", set()):
                    try:
                        url = (
                            "https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/"
                            f"?key={self.API_KEY}&steamids={sid}"
                        )
                        import httpx
                        resp = await httpx.AsyncClient(timeout=10).get(url)
                        data = resp.json()
                        players = data.get('response', {}).get('players', [])
                        if players and players[0].get('personaname'):
                            player_name = players[0]['personaname']
                            if sid not in self.last_states:
                                self.last_states[sid] = {}
                            self.last_states[sid]['name'] = player_name
                        else:
                            player_name = sid
                    except Exception:
                        player_name = sid
                    # 标记已查过API，避免重复查
                    if not hasattr(self, "_steam_log_api_checked"):
                        self._steam_log_api_checked = set()
                    self._steam_log_api_checked.add(sid)
                else:
                    # 已查过API但没查到，直接用sid
                    player_name = sid
        name_map[sid] = player_name
    lines = ["[最近24小时游玩记录]"]
    for sid in self.STEAM_IDS:
        player_name = name_map[sid]
        lines.append(f"[{player_name}]")
        user_logs = logs_by_user[sid]
        if not user_logs:
            lines.append("  无游玩记录\n")
        else:
            user_logs.sort(key=lambda x: x["end_time"], reverse=True)
            for item in user_logs:
                dt = datetime.fromtimestamp(item["end_time"])
                time_str = dt.strftime("%m-%d %H:%M")
                game = item["game_name"]
                duration_min = item['duration']
                if duration_min < 60:
                    duration_str = f"{duration_min:.1f}分钟"
                else:
                    duration_str = f"{duration_min/60:.1f}小时"
                lines.append(f"  {time_str} 游玩了 {duration_str} {game}")
            lines.append("")
    yield event.plain_result("\n".join(lines))
