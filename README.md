# Steam 状态监控 AstrBot 插件

本插件是为AstrBot编写的，可定时监控指定Steam玩家的在线与游戏状态，并在状态变更时推送通知。


## 快速上手
1. 将插件放置在`\AstrBot\data\plugins`目录下
2. 在AstrBot网页后台的配置中填写（[Steam Web API](https://steamcommunity.com/dev/apikey)）
3. 使用 `/steam addid [SteamID]`指令，或在配置中添加需要监控的64位Steam玩家ID
4. 使用 `/steam on`指令开启监控 


## 详细说明
1. **填写配置**
   - 复制本目录到 AstrBot 的 `plugins/` 目录下。
   - 编辑 `config.json`，填写你的 Steam Web API Key 及需要监控的 SteamID 列表。

2. **配置参数说明**
   - `steam_api_key`：你的（[ Steam Web API Key](https://steamcommunity.com/dev/apikey)）
   - `steam_ids`：要监控的 SteamID（64位数字字符串），可填写多个
   - `poll_interval_sec`：轮询间隔（秒），默认60秒
   - `retry_times`：API请求失败时的重试次数
   - `notify_group_id`：如需推送到指定群，可填写群ID，否则留空--（不建议使用此功能）

3. **常用指令**
   - `/steam on` 启动监控
   - `/steam list` 查看所有玩家状态
   - `/steam addid [SteamID]` 添加监控对象
   - `/steam delid [SteamID]` 删除监控对象
   - `/steam openbox [SteamID]` 查询用户信息等
   - `/steam config` 查看当前配置
   - `/steam help` 查看全部指令

其他：获取速度与是否成功获取steam数据取决于网络环境。建议通过魔法手段来保证稳定的查询状态。
>
