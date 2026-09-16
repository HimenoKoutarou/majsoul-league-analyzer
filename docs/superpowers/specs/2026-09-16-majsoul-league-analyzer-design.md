# 雀魂联赛数据展示与分析网站 · 设计文档

日期：2026-09-16
状态：已与需求方确认总体方案，待实现

## 1. 背景与目标

为雀魂（Majsoul）四人麻将联赛构建一个自部署的数据展示与分析网站：

- 支持联赛品牌自定义（logo、名称）与队伍名单管理
- 通过**赛事场（DHS）接口批量同步**对局数据，初始化联赛信息
- 对局列表展示概况，可展开查看**逐局详情**（宝牌、立直、鸣牌、和牌役种、点数变动）
- 按**个人**或**队伍**两个维度聚合分析（顺位分布、和牌率、放铳率、立直率、副露率、打点、役种频次、积分推移）
- 联赛赛制：每场对局四队各出一人，按顺位积分

## 2. 范围与非目标（v1）

**非目标**：
- 牌谱回放器（逐巡动画重放）——后续版本
- 多联赛实例（单库单联赛，`league` 表仅一行；架构上不阻止扩展）
- 三人麻将（sanma）完整支持（解析器保留北漂/拔牌代码路径，统计口径按四麻设计）
- 多语言界面（仅中文）、多管理员/权限体系（单管理员 token）
- 流局听牌率统计（数据源在全场听牌/全场未听时不给出 delta，无法可靠推断，见 §6.4）

## 3. 总体架构

```
雀魂DHS网关 (wss) ────┐                       ┌→ 公开页面（只读，无需登录）
雀魂大厅网关 (wss) ────┼→ 同步引擎 → SQLite ─→ FastAPI ─┤
ninklang.tech (https) ┘  (统一解析为         │
                         天凤格式牌谱)       └→ 管理后台（Bearer token）
```

- **单进程部署**：`pip install -r requirements.txt && python run.py`
- 后端：Python 3.10+，FastAPI + uvicorn + SQLAlchemy 2.x
- 数据库：默认 SQLite（WAL 模式），通过 `DATABASE_URL` 环境变量可切 PostgreSQL（不用 SQLite 特有 SQL）
- 前端：原生 JS（ES Modules）+ ECharts（本地 vendor，不走 CDN）+ 单一 CSS，**无构建步骤**，FastAPI StaticFiles 托管
- 两条数据通道产出**同一种内部格式**（天凤牌谱格式，见 §5），后续统计与展示只面对这一种格式

## 4. 数据模型（SQLAlchemy 模型）

| 表 | 字段 | 说明 |
|---|---|---|
| `league` | id, name, logo_path, description, contest_id, score_rule(JSON), created_at | 单行；score_rule 见 §10 |
| `teams` | id, name, short_name, logo_path, color, sort_order | color 为主题色（#RRGGBB），前端座位/曲线着色用 |
| `players` | **account_id(PK, 雀魂账号ID)**, nickname, team_id(FK), contest_registered(bool) | 昵称仅展示用，归属以 account_id 为准 |
| `games` | **uuid(PK, 牌谱uuid)**, league_id, contest_id, mode(规则JSON), start_time, end_time, raw_head(JSON), fetched_via('dhs'/'ninklang'), synced_at | mode 存红宝数量等；raw_head 存对局头（四家段位/rate等） |
| `game_players` | (game_uuid, seat) PK, player_account_id, final_score, rank, pt(雀魂pt), stats(JSON) | stats 为该场技术统计（见 §9），ingest 时计算 |
| `kyokus` | id, game_uuid, index, round(JSON: [kyoku, honba, riichi_bang]), data(JSON, 天凤格式整局数组), summary(JSON) | summary 含结果类型/和了家/放铳家/各家delta，列表页快速渲染用 |
| `sync_runs` | id, channel('dhs'/'ninklang'), started_at, finished_at, status, games_added, error(JSON/TEXT) | 管理后台展示同步历史与错误 |

索引：`games(end_time)`、`kyokus(game_uuid, index)`、`game_players(player_account_id)`、`game_players(game_uuid)`。

schema 演进：`create_all` + `_meta` 表记录 schema_version，后续版本用轻量迁移脚本按需 ALTER。

## 5. 内部统一牌谱格式（天凤格式）

所有牌谱统一存储为**天凤 JSON 变体**（与雀魂牌谱屋/ninklang 返回一致，已用真实样本验证）。每局（kyoku）是一个数组：

```
[
  [kyoku_index, honba, riichi_sticks],   # 0: 场次，kyoku_index = 4*chang+ju（0起）
  [25000, 25000, 25000, 25000],          # 1: 局始点数
  [dora...],                             # 2: 宝牌指示牌
  [ura...] 或 [],                        # 3: 里宝（和了局才有）
  [haipai0...13], [draws0], [discards0], # 4-6: 东家 配牌/摸牌序列/切牌序列
  [haipai1], [draws1], [discards1],      # 7-9: 南家
  ...                                    # 10-15: 西家、北家
  result                                 # 16: 结果
]
```

- **牌编码**：`11-19`=万，`21-29`=饼，`31-39`=索，`41-47`=字（东南西北白发中），`51/52/53`=红5万/饼/索
- **切牌序列**：数字=手切；`60`=摸切；`"r{tile}"`=立直宣言牌（如 `"r12"`）；`0`=大明杠占位
- **摸牌序列**：数字=摸牌；`"c{a}{b}{p}"`=吃；`"{a}p{b}"`=碰（p 后是喂牌）；`"m"` 前缀=大明杠；`"a"` 后缀=暗杠；`"k"` 前缀=加杠；`"f44"`=拔北（sanma）
- **result**：
  - `["和了", [delta×4], [winner, loser, pao, "符飜点数串", "役名(飜数)"...]]`（double ron 时追加多组 delta+详情）；`loser`=放铳家（自摸时=和了家）；`pao`=包牌家（无包牌时=和了家）
  - `["流局", [delta×4]]`；`["流し満貫", [delta×4]]`
  - 途中流局：`["九種九牌"]` / `["四風連打"]` / `["四家立直"]` / `["四開槓"]` / `["三家和"]`

对局级字段（`games.raw_head` + `games.mode`）：`name[4]`、`sc[8]`（终局点数+pt/1000 交替）、`rule`（aka51/52/53 红宝数、disp）、`title[1]` 时间、`dan`、`rate`。

## 6. 数据接入

### 6.1 通道 A：ninklang.tech 免登录中转（分享链接 → 天凤JSON）

已实测可用（3 秒返回完整数据），作为兜底通道与沙箱开发期数据源：

1. `POST https://ninklang.tech/api/v1/desktop/requests`，body `{"share_url": "<雀魂分享链接>"}` → `{request_id, request_token(rq_...), status, poll_after_ms}`
2. 轮询 `GET /api/v1/requests/{request_id}`，头 `Authorization: Request {request_token}`，状态机 `queued/fetching/retry_wait/converting → ready | failed/expired`
3. `GET /api/v1/requests/{request_id}/result` → 完整天凤 JSON（校验含 `name`、`log`、`ver`）
4. 限制：轮询间隔遵循 `poll_after_ms`（0.25~5s 钳制）、总超时 900s、结果 ≤64MB

### 6.2 通道 B：DHS 赛事场同步（主通道，需组织者账号）

两条 WebSocket 连接，协议均为雀魂 liqi RPC（帧格式：请求 `0x02 + seq(2B LE) + Wrapper{name, data}`，响应 `0x03 + seq + Wrapper`，Wrapper 为 protobuf `liqi.proto` 中的消息）：

**B1. DHS 网关** `wss://common-v2.maj-soul.com/contest_ws_gateway`
1. `loginContestManager(account, password=hmac_sha256(密码, "lailai").hex, type=0)`
2. `manageContest(unique_id=赛事场id)` → contest 信息（名称/规则）→ 初始化联赛
3. `fetchContestPlayer` → 参赛名单（account_id + 昵称）→ 初始化 players（conteset_registered=1）
4. `fetchContestGameRecords` → 对局列表（uuid、结果概要）→ 增量入库 games（按 uuid 去重）

**B2. 大厅网关** `wss://{gateway}/gateway`（由 `game.maj-soul.com/1/version.json` → `config.json` → `route-*.maj-soul.com/api/v0/recommend_list?service=ws-gateway` 动态发现）
1. `login(account, password=hmac_sha256(密码,"lailai").hex, device={is_browser:true}, random_key, client_version_string="web-{version}"})`（组织者账号）
2. 对 B1 得到的每个新 uuid：`fetchGameRecord(game_uuid, client_version_string)` → `head`（对局头）+ `data`（`GameDetailRecords` protobuf）
3. 解析 `GameDetailRecords`（新版为 `actions[].result` 内嵌 Wrapper，旧版为 `records`）→ 天凤格式（见 §6.3）

同步为**两阶段**：先列表（快，秒级），再逐场取详情（慢，带进度条，逐条入库，失败记录到 sync_runs.error 不中断整体）。

### 6.3 牌谱解析器（protobuf → 天凤格式）

处理 9 种 record：`RecordNewRound / RecordDealTile / RecordDiscardTile / RecordChiPengGang / RecordAnGangAddGang / RecordBaBei / RecordLiuJu / RecordNoTile / RecordHule`。

实现参考 tensoul（PyPI，源码已留存在 `.tensoul_ref/`，含 parser.py/model.py）。**实现前须核查 tensoul 许可证**：若为宽松许可（MIT 等）直接改写并保留署名；若为 AGPL 则按其逻辑重写（本项目也将以 AGPL 兼容许可发布，规避问题）。

关键语义（来自 tensoul 源码，防止踩坑）：
- 庄家第一切：配牌 13 张中最后一张视为庄家第 14 张摸牌，其手切特殊处理为摸切
- 立直宣言在 `RecordDiscardTile.is_liqi`，立直成立要等下一次 `RecordDealTile/RecordChiPengGang` 确认（途中流局时未成立的立直不计）
- 大明杠在切牌序列插 `0` 占位；暗杠/加杠记录在切牌序列；杠后宝牌指示牌更新取 `doras` 最长列表
- `RecordHule`：点数变动需自行计算（供托立直棒 `1000*(局中立直数+局始立直棒)`、本场费 `100*本场`、双响时分摊；`point_zimo_xian/qin`、`point_rong`、包牌大三元/大四喜责任払）；自摸时 loser=和了家；ninklang 通道无此问题（delta 直接给出）
- liqi 协议文件：从可达的 `game.maj-soul.com/1/v{ver}/code.js` 提取（protobufjs 内嵌 descriptor），构建脚本生成 `liqi_pb2.py` 后提交仓库；雀魂大版本更新时重跑脚本

### 6.4 玩家识别与入库

- 以雀魂 `account_id` 为主键。天凤格式（ninklang 通道）**不含 account_id**，仅有昵称：入库时按昵称精确匹配已注册玩家（来自赛事场名单）；匹配不到的对局玩家自动建 `players` 行（contest_registered=0，管理后台可认领/合并）
- 昵称匹配歧义（重名）时：入库挂起该场，管理后台人工确认后重放
- 一场对局同队多人出现（非标准赛制录入）不阻止入库，统计按 player 归属自然聚合

## 7. API 设计

公开（`/api`，只读）：
- `GET /api/league` → 联赛信息+积分规则
- `GET /api/teams` → 队伍列表（含成员）
- `GET /api/standings?by=team|player` → 积分榜（§10 口径）
- `GET /api/games?page=&size=` → 对局分页列表（概况：时间/四家+队伍/终局点数/顺位/pt）
- `GET /api/games/{uuid}` → 对局详情（含逐局天凤数据+summary）
- `GET /api/stats?by=player|team&id=` → 技术统计（§9）
- `GET /api/stats/yaku?by=player|team&id=` → 役种频次

管理（`/api/admin`，`Authorization: Bearer {ADMIN_TOKEN}`，token 取自环境变量或首启自动生成写入 `.admin_token` 文件）：
- `PUT /api/admin/league`（名称/logo上传/简介）
- `POST /api/admin/league/init-from-contest` `{contest_id, username, password}` → B1 流程
- `POST/PUT/DELETE /api/admin/teams`、`POST /api/admin/players`（含按昵称搜索雀魂账号：DHS `searchAccountByNickname`）
- `PUT /api/admin/score-rule`
- `POST /api/admin/sync` → 启动 B 通道同步（后台线程，进度 `GET /api/admin/sync/status`）
- `POST /api/admin/games/ingest` `{share_url}` → A 通道录入
- `GET /api/admin/sync-runs` → 同步历史

安全约束：密码仅请求体内出现、不落库不回显；日志对密码/token 脱敏；上传 logo 限制类型（png/jpg/webp/svg）与大小（≤2MB），文件名随机化。

## 8. 前端页面

页面（`web/` 下多页应用，共享 `js/api.js`、`js/tiles.js`、`css/main.css`）：

1. **index.html 首页**：联赛 logo+名称+简介；队伍积分榜卡片；最近 5 场对局
2. **games.html 对局页**：列表行（时间 / 四家昵称+队伍色点 / 终局点数 / 顺位徽章 / 雀魂pt）→ 点击行展开详情：**点数推移折线图**（ECharts）+ 逐局折叠面板（局标题"东一局0本"、宝牌/里宝牌图、各立直家标记（含巡目）、鸣牌标记、和牌块：役种列表+符飜+点数变动+放铳者，流局面板显示各听牌家点数变动）
3. **analysis.html 分析页**：维度切换 玩家/队伍 → 对象选择 → 顺位分布（堆叠柱）、攻防指标卡（和牌率/放铳率/立直率/副露率/平均打点/平均铳点/平均顺位）、积分与素点推移（line）、役种频次（横向 bar）
4. **admin.html 管理后台**：联赛设置 / 队伍与名单（拖拽或下拉分配）/ 积分规则表单 / 同步按钮+进度+历史 / 分享链接录入

牌面渲染：纯 CSS 文字牌（万饼索着色，红5高亮，字牌用汉字），无图片资源依赖；小屏响应式（列表页可横向滚动）。

## 9. 统计口径

ingest 时按场计算进 `game_players.stats`（JSON），聚合在查询层做 sum/divide：

| 指标 | 定义 |
|---|---|
| 平均顺位 | Σrank / 场数 |
| 和牌率 | 和了人次 / 参与局数（分母=该玩家坐过的 kyoku 数，含途中流局局） |
| 自摸率 | 自摸人次 / 和了人次 |
| 放铳率 | 放铳人次 / 参与局数 |
| 立直率 | 立直人次 / 参与局数（立直成立口径，见 §6.3） |
| 副露率 | 吃/碰/明杠/加杠人次 ≥1 的局数 / 参与局数（**按局去重**，一局多鸣算 1） |
| 平均打点 | Σ和牌点数 / 和了人次（不含供托立直棒本场费） |
| 平均铳点 | Σ放铳点数 / 放铳人次 |
| 役种频次 | 按役名计数（含宝牌/里宝/红宝，分别计） |

和牌点数口径：荣和取 `point_rong`，自摸取三家支付合计；供托与本场费不计入打点（单独存 `riichi_sticks_won` 可选展示）。统计层不重新算点数，优先信任牌谱给出的 delta，打点从 delta 反推（和了家 delta - 供托 - 本场费）。

## 10. 积分规则

`league.score_rule`（JSON，管理后台可改，改后历史积分即时重算——积分不入库，每次查询现算）：

```json
{
  "rank_points": [90, 45, 0, -45],
  "allow_negative": true,
  "tiebreak": "raw_points"
}
```

- 队伍积分 = 该队所有玩家各场顺位分之和；并列时按素点和（终局点数总和）决胜
- 同步展示雀魂 pt（`games` 内原值）作为参考列

## 11. 配置与安全

- 环境变量：`DATABASE_URL`（默认 `sqlite:///data/league.db`）、`ADMIN_TOKEN`（缺省则首启生成到 `.admin_token`）、`HOST/PORT`（默认 0.0.0.0:8000）
- 组织者凭据不持久化（每次同步请求时传入），仅内存中使用
- ninklang 与雀魂网关均可能限流：DHS 同步对 fetchGameRecord 做 0.5~1s 间隔串行；ninklang 遵循 poll_after_ms

## 12. 错误处理

- 同步引擎：单场失败不中断（记 sync_runs.error：{uuid, stage, message}）；DHS/lobby 连接失败按 1s/2s/5s 退避重试 3 次
- 解析器遇到非法数据：抛 `ParseError`，整场标记失败，原始数据留存 raw 字段便于排查
- 前端：API 5xx 统一 toast；对局详情渲染对缺失字段（如无里宝）容错
- 立直牌谱版本兼容：`GameDetailRecords.version < 210715` 走旧 `records` 路径，其余走 `actions` 路径（tensoul 同款分支）

## 13. 测试策略

- **pytest 单元**：
  - 牌编码/解码、天凤格式解析（`sample_paipu.json` 真实样本做 fixture：9 局、含流局/和了/立直/鸣牌断言）
  - 统计提取：用样本对局断言和牌数、立直数、副露局数、打点合计
  - 积分计算：顺位分表+平分决胜
  - 协议帧 codec：Wrapper 打包/解包 roundtrip
- **API 测试**：FastAPI TestClient + 临时 SQLite，覆盖公开端点与管理端点鉴权
- **协议客户端**：DHS/lobby 客户端抽象为接口，同步服务用 fake client 测试编排逻辑（两阶段、去重、错误累积）
- **部署环境验收**（沙箱跑不了，交付清单形式）：真实赛事场凭据跑通 B1/B2；沙箱内用 A 通道 + 样本数据验收其余全部功能

## 14. 风险与对策

| 风险 | 影响 | 对策 |
|---|---|---|
| 沙箱连不上雀魂网关（已实测：TLS 握手被掐） | B 通道无法在本环境联调 | 协议按 UvUManager/tensoul 成熟参考实现；fake client 测试编排；交付后部署环境验收 |
| liqi.proto 提取脚本失败或版本漂移 | B 通道不可用 | proto 编译产物入库；社区维护版本对照；雀魂更新时重跑提取 |
| ninklang 第三方服务限流/停服 | A 通道不可用 | B 通道为主；错误信息明确提示 |
| 昵称匹配错人 | 统计归属错误 | 以 account_id 为主键；歧义挂起人工确认；管理后台可修正归属并重算 |
| tensoul 许可证 | 合规 | 实现前核查（§6.3） |

## 15. 目录结构

```
run.py                     # 入口：uvicorn 启动 + create_all + .admin_token
requirements.txt
app/
  main.py                  # FastAPI 装配（路由/静态/异常）
  config.py                # 环境变量
  db.py                    # engine/session
  models.py                # SQLAlchemy 模型
  api/public.py  api/admin.py
  services/
    paipu/common.py        # 牌工具 + 天凤格式类型
    paipu/tenhou_ingest.py # 天凤JSON → 校验入库+stats
    paipu/majsoul_parse.py # protobuf records → 天凤格式
    ninklang.py            # 通道A客户端
    majsoul/client.py      # liqi 帧codec + DHS/lobby 客户端（接口化）
    majsoul/liqi.proto + liqi_pb2.py
    sync.py                # 同步编排（两阶段）
    stats.py               # 聚合查询
web/
  index.html games.html analysis.html admin.html
  js/ css/ vendor/echarts.min.js  uploads/
tests/
  conftest.py  test_tiles.py test_parse.py test_stats.py test_score.py test_api.py test_sync.py
docs/superpowers/specs/    # 本文档
sample_paipu.json          # 真实样本 fixture（用户提供链接抓取）
.tensoul_ref/              # 参考源码（不入库，gitignore）
```

## 16. 部署

- 依赖：Python ≥3.10；`pip install -r requirements.txt`
- 启动：`python run.py`（或 `uvicorn app.main:app --host 0.0.0.0 --port 8000`）
- 数据目录：`data/league.db`、`web/uploads/`；备份即拷贝这两个位置
- 反代/HTTPS 由使用方自行加（Caddy/Nginx）；应用本身监听 HTTP
