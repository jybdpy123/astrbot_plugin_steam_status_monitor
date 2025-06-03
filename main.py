from astrbot.api.star import Star, register, Context
from astrbot.api import logger
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.event import MessageChain
from astrbot.api.message_components import Plain
import json
import time
import httpx
import asyncio
import os
import random
from .openbox import handle_openbox  # 新增导入
from .game_log import GameLogManager, handle_steam_log  # 新增导入
from .steam_list import handle_steam_list  # 新增导入

@register(
    "steam_status_monitor",
    "Maoer",
    "Steam状态监控插件",
    "1.0.0",
    "https://github.com/Maoer233/steam_status_monitor"
)
class SteamStatusMonitor(Star):
    def __init__(self, context: Context, config=None):
        super().__init__(context)
        self.context = context
        self.last_states = {}
        self.start_play_times = {}
        self.running = False
        self.notify_session = None
        self._game_name_cache = {}
        # 统一使用 AstrBot 配置系统
        self.config = config or {}
        # 兼容旧逻辑，若 config 为空则尝试读取 config.json（可选，建议后续移除）
        if not self.config:
            try:
                config_path = os.path.join(os.path.dirname(__file__), 'config.json')
                with open(config_path, 'r', encoding='utf-8') as f:
                    self.config = json.load(f)
            except Exception as e:
                logger.error(f"steam_status_monitor 配置读取失败: {e}")
                self.config = {}
        # 读取配置项，提供默认值
        self.API_KEY = self.config.get('steam_api_key', '')
        self.STEAM_IDS = self.config.get('steam_ids', [])
        self.POLL_INTERVAL = self.config.get('poll_interval_sec', 10)
        self.RETRY_TIMES = self.config.get('retry_times', 3)  # 新增：重试次数
        self.GROUP_ID = self.config.get('notify_group_id', None)
        self.game_log = GameLogManager()  # 新增：游戏日志管理器
        # 启动保活心跳任务（每30分钟调用一次 get_status）
        asyncio.create_task(self.keep_alive_task())

    async def keep_alive_task(self):
        '''定时调用 get_status 保持NapCat连接活跃，减少掉线概率'''
        while True:
            try:
                await asyncio.sleep(1800)  # 30分钟
                platform = self.context.get_platform("aiocqhttp")  # 或根据你的平台类型调整
                if hasattr(platform, "get_client"):
                    client = platform.get_client()
                    if hasattr(client, "api"):
                        await client.api.call_action("get_status")
                        logger.info("NapCat保活心跳已发送。")
            except Exception as e:
                logger.warning(f"NapCat保活心跳失败: {e}")
            await asyncio.sleep(self.POLL_INTERVAL)

    async def fetch_player_status(self, steam_id, retry=None):
        '''拉取单个玩家的 Steam 状态，失败自动重试多次并指数退避'''
        url = (
            "https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/"
            f"?key={self.API_KEY}&steamids={steam_id}"
        )
        delay = 1
        retry = retry if retry is not None else self.RETRY_TIMES
        for attempt in range(retry):
            logger.info(f"正在查询 SteamID: {steam_id}，第{attempt+1}次尝试")  # 改为 info
            async with httpx.AsyncClient(timeout=15) as client:
                try:
                    resp = await client.get(url)
                    if resp.status_code != 200:
                        raise Exception(f"HTTP {resp.status_code}")
                    try:
                        data = resp.json()
                    except Exception as je:
                        raise Exception(f"JSON解析失败: {je}")
                    if not data.get('response') or not data['response'].get('players') or not data['response']['players']:
                        raise Exception("响应中无玩家数据")
                    player = data['response']['players'][0]
                    logger.info(f"SteamID: {steam_id} 查询成功。")  # 改为 info
                    return {
                        'name': player.get('personaname'),
                        'gameid': player.get('gameid'),
                        'lastlogoff': player.get('lastlogoff'),
                        'gameextrainfo': player.get('gameextrainfo'),
                        'personastate': player.get('personastate', 0)
                    }
                except Exception as e:
                    logger.warning(f"拉取 Steam 状态失败: {e} (SteamID: {steam_id}, 第{attempt+1}次重试)")
                    if attempt < retry - 1:
                        await asyncio.sleep(delay)
                        delay *= 2
        logger.error(f"SteamID {steam_id} 状态获取失败，已重试{retry}次")
        return None

    async def get_chinese_game_name(self, gameid, fallback_name=None):
        '''
        优先通过 Steam 商店API获取游戏中文名（l=schinese），若无则返回英文名（l=en），最后才返回 fallback_name 或“未知游戏”
        '''
        if not gameid:
            return fallback_name or "未知游戏"
        gid = str(gameid)
        if gid in self._game_name_cache:
            return self._game_name_cache[gid]
        # 优先查中文名（l=schinese），再查英文名（l=en）
        url_zh = f"https://store.steampowered.com/api/appdetails?appids={gid}&l=schinese"
        url_en = f"https://store.steampowered.com/api/appdetails?appids={gid}&l=en"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                # 查中文名
                resp_zh = await client.get(url_zh)
                data_zh = resp_zh.json()
                info_zh = data_zh.get(gid, {}).get("data", {})
                name_zh = info_zh.get("name")
                if name_zh:
                    self._game_name_cache[gid] = name_zh
                    return name_zh
                # 查英文名
                resp_en = await client.get(url_en)
                data_en = resp_en.json()
                info_en = data_en.get(gid, {}).get("data", {})
                name_en = info_en.get("name")
                if name_en:
                    self._game_name_cache[gid] = name_en
                    return name_en
        except Exception as e:
            logger.warning(f"获取游戏名失败: {e} (gameid={gid})")
        # 不缓存 fallback，让下次还能重试
        return fallback_name or "未知游戏"

    async def check_status_change(self):
        '''轮询检测玩家状态变更并推送通知，并打印所有玩家状态'''
        logger.info("开始轮询所有玩家状态")  # 改为 info
        msg_lines = []
        now = int(time.time())
        for sid in self.STEAM_IDS:
            logger.info(f"检查 SteamID: {sid}")  # 改为 info
            status = await self.fetch_player_status(sid)
            if not status:
                logger.warning(f"SteamID: {sid} 查询失败，跳过")
                msg_lines.append(f"❌ [{sid}] 获取失败\n")
                continue
            prev = self.last_states.get(sid)
            name = status.get('name') or sid
            gameid = status.get('gameid')
            game = status.get('gameextrainfo')
            lastlogoff = status.get('lastlogoff')
            personastate = status.get('personastate', 0)
            # 获取中文游戏名（如有gameid）
            zh_game_name = await self.get_chinese_game_name(gameid, game) if gameid else (game or "未知游戏")
            # 检测游戏切换（包括从A->B，A->无，None->B）
            prev_gameid = prev.get('gameid') if prev else None
            prev_game = prev.get('gameextrainfo') if prev else None
            if gameid and prev_gameid and gameid != prev_gameid:
                # 游戏切换（A->B）
                prev_zh_game_name = await self.get_chinese_game_name(prev_gameid, prev_game) if prev_gameid else (prev_game or "未知游戏")
                # 结束上一个游戏
                if sid in self.start_play_times:
                    duration_min = (now - self.start_play_times[sid]) / 60
                    if duration_min < 60:
                        duration_str = f"{duration_min:.1f}分钟"
                    else:
                        duration_str = f"{duration_min/60:.1f}小时"
                    now_hour = time.localtime().tm_hour
                    tail = ""
                    if 1 <= now_hour < 6:
                        night_tails = [
                            "解锁称号：速通人生（0.5分钟版）",
                            "你是夜猫子吗？咱可是正经猫娘喵，要睡觉的！",
                            "肝游戏不如肝工作，至少你老板会给你五险一金",
                            "你妈半夜起来看你还亮着屏幕，现在已经在抄家伙了喵",
                            "解锁称号：现实技能点全点在了虚拟肝上",
                            "你再不睡，你妈今晚就得拿电蚊拍给你开机重启了喵",
                            "再玩下去，咱怕你不是通关，是通灵了喵",
                            "你还在玩吗？咱都猫叫一晚上了喵～",
                            "你玩得再久，头发也不会原地复生的喵",
                            "主人再打下去，咱就得学急救知识啦，虽然咱是猫不会按人中喵～",
                            "主人你再不睡，咱今晚就罢工不喵叫了，气鼓鼓(｡•ˇ‸ˇ•｡)",
                            "解锁称号：‘玩到天明’，友情提示：你明天死定了喵～"
                        ]
                        tail = random.choice(night_tails)
                    else:
                        if duration_min < 5:
                            tail = "这也叫玩？开机都没热！"
                        elif duration_min < 10:
                            tail = "疲软无力的~杂鱼~"
                        elif duration_min < 30:
                            tail = "热身运动，指尖刚热"
                        elif duration_min < 60:
                            tail = "轻松娱乐一下，刚刚好喵~"
                        elif duration_min < 120:
                            tail = "认认真真地沉浸了一阵子喵~"
                        else:
                            tail = "根本停不下来喵！眼睛都要冒烟啦！"
                    msg = f"👋 {name} 不玩 {prev_zh_game_name} 了\n游玩时间 {duration_str} {tail}"
                    logger.info(f"{msg}，推送通知")
                    try:
                        msg_chain = MessageChain([Plain(msg)])
                        await self.context.send_message(self.notify_session, msg_chain)
                    except Exception as e:
                        logger.error(f"推送结束游戏消息失败: {e}")
                    # 仅当游玩时间大于10分钟才记录日志
                    if duration_min > 10:
                        await self.game_log.record_log(
                            steamid=sid,
                            player_name=name,
                            gameid=prev_gameid,
                            game_name=prev_zh_game_name,
                            duration=duration_min,
                            end_time=now
                        )
                # 立即记录新游戏开始时间
                self.start_play_times[sid] = now
                # 推送新游戏开始
                try:
                    if self.notify_session:
                        msg_chain = MessageChain([Plain(f"🟢 {name} 开始玩 {zh_game_name} 了！")])
                        await self.context.send_message(self.notify_session, msg_chain)
                    else:
                        logger.error("未设置推送会话，无法发送消息")
                except Exception as e:
                    logger.error(f"推送新开始游戏消息失败: {e}")
            # 玩家新开始游戏（无->B）
            elif gameid and (not prev or not prev.get('gameid')):
                logger.info(f"{name} 开始玩 {zh_game_name} 了，推送通知")
                try:
                    if self.notify_session:
                        msg_chain = MessageChain([Plain(f"🟢 {name} 开始玩 {zh_game_name} 了！")])
                        await self.context.send_message(self.notify_session, msg_chain)
                    else:
                        logger.error("未设置推送会话，无法发送消息")
                except Exception as e:
                    logger.error(f"推送新开始游戏消息失败: {e}")
                self.start_play_times[sid] = now
            # 玩家退出游戏（A->无）
            if prev and prev.get('gameid') and not gameid:
                prev_gameid = prev.get('gameid')
                prev_game = prev.get('gameextrainfo')
                zh_prev_game_name = await self.get_chinese_game_name(prev_gameid, prev_game) if prev_gameid else (prev_game or "未知游戏")
                try:
                    if self.notify_session:
                        if sid in self.start_play_times:
                            duration_min = (now - self.start_play_times[sid]) / 60
                            if duration_min < 60:
                                duration_str = f"{duration_min:.1f}分钟"
                            else:
                                duration_str = f"{duration_min/60:.1f}小时"
                            now_hour = time.localtime().tm_hour
                            tail = ""
                            if 1 <= now_hour < 6:
                                night_tails = [
                                    "解锁称号：速通人生（0.5分钟版）",
                                    "你是夜猫子吗？咱可是正经猫娘喵，要睡觉的！",
                                    "肝游戏不如肝工作，至少你老板会给你五险一金",
                                    "你妈半夜起来看你还亮着屏幕，现在已经在抄家伙了喵",
                                    "解锁称号：现实技能点全点在了虚拟肝上",
                                    "你再不睡，你妈今晚就得拿电蚊拍给你开机重启了喵",
                                    "再玩下去，咱怕你不是通关，是通灵了喵",
                                    "你还在玩吗？咱都猫叫一晚上了喵～",
                                    "你玩得再久，头发也不会原地复生的喵",
                                    "主人再打下去，咱就得学急救知识啦，虽然咱是猫不会按人中喵～",
                                    "主人你再不睡，咱今晚就罢工不喵叫了，气鼓鼓(｡•ˇ‸ˇ•｡)",
                                    "解锁称号：‘玩到天明’，友情提示：你明天死定了喵～"
                                ]
                                tail = random.choice(night_tails)
                            else:
                                if duration_min < 5:
                                    tail = "这也叫玩？开机都没热！"
                                elif duration_min < 10:
                                    tail = "疲软无力的~杂鱼~"
                                elif duration_min < 30:
                                    tail = "热身运动，指尖刚热"
                                elif duration_min < 60:
                                    tail = "轻松娱乐一下，刚刚好喵~"
                                elif duration_min < 120:
                                    tail = "认认真真地沉浸了一阵子喵~"
                                else:
                                    tail = "根本停不下来喵！眼睛都要冒烟啦！"
                            msg = f"👋 {name} 不玩 {zh_prev_game_name} 了\n游玩时间 {duration_str} {tail}"
                            logger.info(f"{msg}，推送通知")
                            try:
                                msg_chain = MessageChain([Plain(msg)])
                                await self.context.send_message(self.notify_session, msg_chain)
                            except Exception as e:
                                logger.error(f"推送退出游戏消息失败: {e}")
                            # 仅当游玩时间大于10分钟才记录日志
                            if duration_min > 10:
                                await self.game_log.record_log(
                                    steamid=sid,
                                    player_name=name,
                                    gameid=prev_gameid,
                                    game_name=zh_prev_game_name,
                                    duration=duration_min,
                                    end_time=now
                                )
                            self.start_play_times.pop(sid, None)
                        else:
                            msg = f"👋 {name} 不玩 {zh_prev_game_name} 了\n游玩时间未知"
                            logger.info(f"{msg}，推送通知")
                            try:
                                msg_chain = MessageChain([Plain(msg)])
                                await self.context.send_message(self.notify_session, msg_chain)
                            except Exception as e:
                                logger.error(f"推送退出游戏消息失败: {e}")
                            # 这里没有游玩时长，不记录日志
                    else:
                        logger.error("未设置推送会话，无法发送消息")
                except Exception as e:
                    logger.error(f"推送退出游戏消息失败: {e}")
            self.last_states[sid] = status
            # 状态文本拼接
            if gameid:
                msg_lines.append(f"🟢【{name}】正在玩 {zh_game_name}\n")
            elif personastate and int(personastate) > 0:
                msg_lines.append(f"🟡【{name}】在线\n")
            elif lastlogoff:
                hours_ago = (now - int(lastlogoff)) / 3600
                msg_lines.append(f"⚪️【{name}】离线\n上次在线 {hours_ago:.1f} 小时前\n")
            else:
                msg_lines.append(f"⚪️【{name}】离线\n")
        logger.warning("自动查询结果：\n" + "".join(msg_lines))
        logger.warning("本轮轮询结束")

    @filter.command("steam on")
    async def steam_on(self, event: AstrMessageEvent):
        '''手动启动Steam状态监控轮询'''
        if not self.API_KEY:
            yield event.plain_result("未配置 Steam API Key，请先在插件配置中填写 steam_api_key。")
            return
        if not self.STEAM_IDS or not any(isinstance(x, str) and x.strip() for x in self.STEAM_IDS):
            yield event.plain_result(
                "未设置监控的 SteamID 列表，请先在插件配置中填写 steam_ids，"
                "或使用 /steam addid [SteamID] 添加要监控的玩家。"
            )
            return
        if self.running:
            yield event.plain_result("Steam监控已在运行。")
            return
        self.running = True
        self.notify_session = event.unified_msg_origin

        # 启动时输出一次 steam list 风格的当前状态，不推送“开始玩游戏了”通知
        msg_lines = []
        now = int(time.time())
        for sid in self.STEAM_IDS:
            status = await self.fetch_player_status(sid)
            if status:
                self.last_states[sid] = status
                if status.get('gameid'):
                    self.start_play_times[sid] = int(time.time())
            name = status.get('name') or sid if status else sid
            gameid = status.get('gameid') if status else None
            game = status.get('gameextrainfo') if status else None
            lastlogoff = status.get('lastlogoff') if status else None
            personastate = status.get('personastate', 0) if status else 0
            # 获取中文游戏名
            zh_game_name = await self.get_chinese_game_name(gameid, game) if gameid else (game or "未知游戏")
            if not status:
                msg_lines.append(f"❌ [{sid}] 获取失败\n")
            elif gameid:
                msg_lines.append(f"🟢 {name} 正在玩 {zh_game_name}\n")
            elif personastate and int(personastate) > 0:
                msg_lines.append(f"🟡 {name} 在线\n")
            elif lastlogoff:
                hours_ago = (now - int(lastlogoff)) / 3600
                msg_lines.append(f"⚪️ {name} 离线\n上次在线 {hours_ago:.1f} 小时前\n")
            else:
                msg_lines.append(f"⚪️ {name} 离线\n")
        yield event.plain_result("".join(msg_lines))
        yield event.plain_result("Steam状态监控启动完成喔！ヾ(≧ω≦)ゞ")
        asyncio.create_task(self.poll_loop())

    @filter.command("steam list")
    async def steam_list(self, event: AstrMessageEvent):
        '''列出所有玩家当前状态'''
        if not self.API_KEY:
            yield event.plain_result("未配置 Steam API Key，请先在插件配置中填写 steam_api_key。")
            return
        if not self.STEAM_IDS:
            yield event.plain_result("未设置监控的 SteamID 列表，请先在插件配置中填写 steam_ids。")
            return
        async for result in handle_steam_list(self, event):
            yield result

    @filter.command("steam config")
    async def steam_config(self, event: AstrMessageEvent):
        '''显示当前插件配置'''
        lines = []
        for k, v in self.config.items():
            lines.append(f"{k}: {v}")
        yield event.plain_result("当前配置：\n" + "\n".join(lines))

    @filter.command("steam set")
    async def steam_set(self, event: AstrMessageEvent, key: str, value: str):
        '''设置配置参数，立即生效（如 steam set poll_interval_sec 30）'''
        if key not in self.config:
            yield event.plain_result(f"无效参数: {key}")
            return
        # 类型转换
        old = self.config[key]
        if isinstance(old, int):
            try:
                value = int(value)
            except Exception:
                yield event.plain_result("类型错误，应为整数")
                return
        elif isinstance(old, float):
            try:
                value = float(value)
            except Exception:
                yield event.plain_result("类型错误，应为浮点数")
                return
        elif isinstance(old, list):
            value = [x.strip() for x in value.split(",") if x.strip()]
        self.config[key] = value
        # 同步到属性
        self.API_KEY = self.config.get('steam_api_key', '')
        self.STEAM_IDS = self.config.get('steam_ids', [])
        self.POLL_INTERVAL = self.config.get('poll_interval_sec', 10)
        self.RETRY_TIMES = self.config.get('retry_times', 3)
        self.GROUP_ID = self.config.get('notify_group_id', None)
        # 保存配置（如支持）
        if hasattr(self.config, "save_config"):
            self.config.save_config()
        yield event.plain_result(f"已设置 {key} = {value}")

    @filter.command("steam addid")
    async def steam_addid(self, event: AstrMessageEvent, steamid: str):
        '''添加SteamID到监控列表'''
        if not steamid or not steamid.isdigit() or len(steamid) < 10:
            yield event.plain_result("请输入有效的 SteamID（64位数字字符串）。")
            return
        if steamid in self.STEAM_IDS:
            yield event.plain_result("该SteamID已存在")
            return
        self.STEAM_IDS.append(steamid)
        self.config['steam_ids'] = self.STEAM_IDS
        if hasattr(self.config, "save_config"):
            self.config.save_config()
        yield event.plain_result(f"已添加SteamID: {steamid}")

    @filter.command("steam delid")
    async def steam_delid(self, event: AstrMessageEvent, steamid: str):
        '''通过SteamID删除监控对象（如 steam delid 7656119xxxxxxx）'''
        if steamid not in self.STEAM_IDS:
            yield event.plain_result("该SteamID不存在")
            return
        self.STEAM_IDS.remove(steamid)
        self.config['steam_ids'] = self.STEAM_IDS
        if hasattr(self.config, "save_config"):
            self.config.save_config()
        yield event.plain_result(f"已删除SteamID: {steamid}")

    @filter.command("steam rs")
    async def steam_rs(self, event: AstrMessageEvent):
        '''清除所有状态并初始化（重启插件用）'''
        self.last_states.clear()
        self.start_play_times.clear()
        self.running = False
        self.notify_session = None
        self._game_name_cache.clear()
        yield event.plain_result("Steam状态监控插件已重置，所有状态已清空。")

    @filter.command("steam help")
    async def steam_help(self, event: AstrMessageEvent):
        '''显示所有指令帮助'''
        help_text = (
            "Steam状态监控插件指令：\n"
            "/steam on - 启动监控\n"
            "/steam off - 停止监控\n"
            "/steam list - 列出所有玩家状态\n"
            "/steam config - 查看当前配置\n"
            "/steam set [参数] [值] - 设置配置参数\n"
            "/steam addid [SteamID] - 添加SteamID\n"
            "/steam delid [SteamID] - 删除SteamID\n"
            "/steam openbox [SteamID] - 查看指定SteamID的全部信息\n"
            "/steam rs - 清除状态并初始化\n"
            "/steam help - 显示本帮助"
        )
        yield event.plain_result(help_text)

    @filter.command("steam openbox")
    async def steam_openbox(self, event: AstrMessageEvent, steamid: str):
        '''查询并格式化展示指定SteamID的全部API返回信息（中文字段名，头像图片附加，位置ID合并，状态字段直观显示）'''
        if not self.API_KEY:
            yield event.plain_result("未配置 Steam API Key，请先在插件配置中填写 steam_api_key。")
            return
        async for result in handle_openbox(self, event, steamid):
            yield result

    @filter.command("steam log")
    async def steam_log(self, event: AstrMessageEvent):
        '''输出24小时内所有玩家的游玩记录'''
        if not self.STEAM_IDS:
            yield event.plain_result("未设置监控的 SteamID 列表，请先在插件配置中填写 steam_ids。")
            return
        async for result in handle_steam_log(self, event):
            yield result

    @filter.command("steam logc")
    async def steam_logc(self, event: AstrMessageEvent, hours: float):
        '''清除指定小时数以外的游玩记录（如 steam logc 2）'''
        try:
            count = await self.game_log.clear_logs_older_than(hours)
            yield event.plain_result(f"已清除{hours}小时以外的游玩记录，剩余{count}条。")
        except Exception as e:
            yield event.plain_result(f"清除日志失败: {e}")

    @filter.command("steam off")
    async def steam_off(self, event: AstrMessageEvent):
        '''停止Steam状态监控轮询'''
        if not self.running:
            yield event.plain_result("Steam监控未在运行。")
            return
        self.running = False
        yield event.plain_result("Steam状态监控已停止。")

    async def poll_loop(self):
        '''定时轮询Steam状态变化'''
        while self.running:
            try:
                await self.check_status_change()
            except Exception as e:
                logger.error(f"轮询Steam状态时发生异常: {e}")
            await asyncio.sleep(self.POLL_INTERVAL)
