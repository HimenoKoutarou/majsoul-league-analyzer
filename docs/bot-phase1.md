# 机器人第一阶段

第一阶段由一个独立的 `bot` 进程组成。主项目负责抓取并入库牌谱，同时在同一数据库事务中写入 `game.created` 事件；机器人只通过只读 HTTP API 消费事件，不会再次登录雀魂或重复抓取牌谱。

## 主项目配置

主项目和机器人进程必须使用同一个 `BOT_API_TOKEN`：

```powershell
$env:BOT_API_TOKEN = "replace-with-a-long-random-token"
python run.py
```

主项目会自动创建 `event_outbox` 表。实时抓取需要另外配置普通雀魂大厅账号：

```powershell
$env:LIVE_SYNC_ENABLED = "true"
$env:MS_USERNAME = "your-majsoul-account"
$env:MS_PASSWORD = "your-majsoul-password"
```

赛事场账号不能替代大厅账号。也可以在管理端设置大厅同步账号和实时同步开关。

## OneBot v11 机器人

机器人使用 OneBot v11 反向 WebSocket，默认连接 `ws://127.0.0.1:6700/onebot/v11/ws`。建议单独打开一个 PowerShell：

```powershell
$env:BOT_API_BASE_URL = "http://127.0.0.1:8000"
$env:BOT_API_TOKEN = "replace-with-the-same-token"
$env:BOT_GROUP_IDS = "123456789,987654321"
$env:ONEBOT_WS_URL = "ws://127.0.0.1:6700/onebot/v11/ws"
$env:ONEBOT_ACCESS_TOKEN = ""
python -m bot
```

默认状态文件为 `bot_data/state.json`，保存事件消费游标。机器人重启后会从上次成功处理的位置继续消费；该目录已加入 `.gitignore`。

本地只验证事件和消息格式时，可以不连接 OneBot：

```powershell
$env:BOT_DRY_RUN = "true"
python -m bot
```

## 酒馆岛 AI 对话

启用后，群内不以 `/` 或 `!` 开头的普通文本会转发到酒馆岛卡片 Token
接口，回复会发送回原群；每个群独立串联对话上下文（上下文由平台按 Token
维护）。

```powershell
$env:JIUGUANDAO_ENABLED = "true"
$env:JIUGUANDAO_BASE_URL = "https://u3670369.nyat.app:24205"
$env:JIUGUANDAO_TOKEN = "your-card-token"
python -m bot
```

## 当前命令

群内支持以下命令：

```text
/帮助
/最新牌谱 [数量]
/对局 <牌谱UUID>
/选手数据 <昵称>
/选手对局 <昵称> [数量]
/队伍数据 <队名>
/队伍对局 <队名> [数量]
/队伍排名
/订阅
/取消订阅
/订阅状态
```

机器人专用接口为 `GET /api/bot/events?after_id=<游标>`，使用 `X-Bot-Token` 或 `Authorization: Bearer <token>` 鉴权。

## 第二阶段补充

群内可以用 `/订阅` 和 `/取消订阅` 管理本群的新牌谱推送。订阅列表与事件游标一起保存在 `BOT_STATE_PATH` 指向的 JSON 文件中；如果从未操作过订阅，则默认使用 `BOT_GROUP_IDS`。查询命令直接调用主项目已有的对局详情、统计和关联对局接口。

## 第三阶段补充

开启每日摘要：

```powershell
$env:BOT_DIGEST_ENABLED = "true"
$env:BOT_DIGEST_TIME = "23:00"
$env:BOT_DIGEST_POLL_INTERVAL = "30"
python -m bot
```

摘要按机器人进程所在机器的本地时间每天发送一次，内容包括当前队伍排名和最近牌谱。摘要发送日期会写入 `BOT_STATE_PATH`，进程重启后不会重复发送同一天的摘要。新增 `/机器人状态` 可以查看事件游标、推送群数量和摘要状态；`/立直数据`、`/最近和铳`、`/最常同桌` 用于查询个人分析的分栏数据。
