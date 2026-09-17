# 悬赏板功能 · 设计文档

日期：2026-09-16
状态：设计与需求方确认，待实现

## 1. 背景与目标

为雀魂联赛数据站新增「悬赏板」，让访问者可以：

- 在目标日期前提交悬赏（管理员审核后展示）
- 在目标日期后提交达成该悬赏的牌谱（管理员审核后计入达成）
- 每个悬赏有可达成次数上限，管理员可随时修改

## 2. 关键决策（已与需求方确认）

| 项 | 决策 |
|---|---|
| 提交人身份 | 匿名 + 雀魂ID（无需注册登录） |
| 达成条件 | 文字描述 + 管理员人工判断 |
| 日期语义 | 目标日期（target_date）：此前可提交悬赏，此后开放牌谱提交 |
| 达成次数 | 可达成上限 max_claims，管理员可改；审核通过一个 claim 计入 1 次 |

## 3. 数据模型（新增 2 表）

### bounties（悬赏）

| 字段 | 类型 | 说明 |
|---|---|---|
| id | int PK | |
| title | str | 标题 |
| description | text | 达成条件描述 |
| reward | str | 奖励说明（可选） |
| target_date | date | 目标日期 |
| max_claims | int | 可达成上限，默认 1 |
| status | str | pending / approved / rejected / closed |
| submitter_nickname | str | 提交人昵称 |
| submitter_account_id | int \| None | 提交人雀魂ID |
| created_at | datetime | |
| reviewed_at | datetime \| None | |

### bounty_claims（牌谱提交 / 达成记录）

| 字段 | 类型 | 说明 |
|---|---|---|
| id | int PK | |
| bounty_id | int FK → bounties.id | |
| share_url | str | 牌谱链接 |
| submitter_nickname | str | 提交人昵称 |
| submitter_account_id | int \| None | 提交人雀魂ID |
| note | text | 备注（可选） |
| status | str | pending / approved / rejected |
| created_at | datetime | |
| reviewed_at | datetime \| None | |

达成计数 = bounties.status == approved 的 claims 数量（动态统计，不冗余存字段）。

## 4. 生命周期与流程

```
用户提交悬赏(公开表单) → status=pending
管理员审核 → approved（展示） / rejected（驳回，不展示）
approved 且 target_date 已到 且 未满额 → 开放提交牌谱(claim)
用户提交 claim → pending
管理员审核 claim → approved（+1 达成） / rejected
approved claims 数 >= max_claims → bounty closed（不再接受提交）
```

- 管理员可随时 `PUT` 修改 `max_claims` 及审核状态。
- 达成次数以 approved claims 为准，不单独手改已达成数。

## 5. 后端 API

公开（无需登录）：
- `GET /api/bounties` 返回已展示悬赏列表（含 claimed_count、是否可提交）
- `POST /api/bounties` 用户提交悬赏（pending）
- `POST /api/bounties/{id}/claims` 用户提交牌谱（pending）
- `GET /api/bounties/{id}/claims` 查看某悬赏的 approved 达成记录

管理（需登录）：
- `GET /api/admin/bounties` 全部悬赏（含待审）
- `PUT /api/admin/bounties/{id}` 审核 / 修改 max_claims
- `GET /api/admin/claims` 全部牌谱提交
- `PUT /api/admin/claims/{id}` 审核牌谱（approve / reject）

## 6. 前端

- 新增公开页 `web/bounties.html`（导航加「悬赏」）：
  - 展示已审核悬赏列表（标题 / 条件 / 奖励 / 目标日期 / 已达 N/上限）
  - 「发起悬赏」表单（标题、条件、奖励、目标日期、昵称、雀魂ID）
  - 目标日期后且未满额的悬赏挂「提交牌谱」表单（牌谱链接、昵称、雀魂ID、备注）
- `web/admin.html` 新增「悬赏」tab：
  - 待审悬赏列表（通过 / 驳回 / 修改上限）
  - 牌谱提交审核列表（通过 / 驳回）

## 7. 范围与非目标（v1）

非目标：
- 用户注册登录体系（匿名 + 雀魂ID 即可）
- 悬赏的自动判达成（人工审核）
- 奖励的自动发放 / 货币体系（reward 仅文本展示）
- 富文本 / 图片悬赏

## 8. 实现要点

- 模型：`app/models.py` 新增 `Bounty`、`BountyClaim` 两个 SQLAlchemy 模型，`init_db` 的 create_all 自动建表（现有 schema 演进方式）。
- 公开路由：`app/api/bounties.py`；管理路由并入 `app/api/admin.py` 或新 `app/api/admin_bounties.py`（沿用 admin 鉴权依赖 `require_admin`）。
- 提交校验：`POST /claims` 需校验 bounty 处于 approved 且 target_date 已到且未满额。
- 计数与关闭：审核 claim approve 时，若 satisfied 达到 max_claims 则置 bounty.status = closed；撤销/驳回不影响计数口径（approved 为准）。
- 前端提交表单需填写「昵称 + 雀魂ID」，雀魂ID 可选但建议必填（提交人身份）。

## 9. 测试

- bounties 模型与 API 的 CRUD + 审核流转 + 满额关闭。
- claims 提交校验（未到目标日期 / 已满额 / 未 approved 时提交应被拒）。
- 管理员审核计数与 max_claims 修改。