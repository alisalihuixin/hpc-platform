# 项目① HPC 作业调度与计费管理平台 · 开发计划（逐日任务分解）

> 版本：v1.0 ｜ 2026-10-13
> 依据：`docs/项目①需求文档_HPC作业调度与计费管理平台.md`（PRD v1.0）
> 排期口径：单人力，1 天 = 1 个工作日（5 天/周），共 8 周 / **D1–D40**
> 里程碑映射沿用 PRD 第 8 节：M1(W1–W2) / M2(W3–W5) / M3(W6–W7) / M4(W8)

---

## 0. 总览

| 里程碑 | 周次 | 天次 | 覆盖模块 | 验收（DoD 汇总） |
|---|---|---|---|---|
| M1 骨架 + 认证 | W1–W2 | D1–D10 | F1 全部、F8.3、工程骨架 | 两角色登录、越权 403、限流生效、登出失效 |
| M2 资源/配额/作业/计量 | W3–W5 | D11–D25 | F2、F3、F4(除 SSE)、F5 | 全链路跑通 + 20 并发不超扣测试绿 |
| M3 SSE + 管理页 | W6–W7 | D26–D35 | F4.7、F2.3 前端、管理页(F2.2/F3.4/F8.1/F8.2) | 双浏览器秒级刷新、管理页可用 |
| M4 报表 + 审计 + 上线 | W8 | D36–D40 | F6、F7 + README + 上线 | 报表可见、审计全覆盖、metrics.md 数据齐 |

> **M1 状态**：骨架与认证已交付（对齐现有提交「M1 骨架与认证」）。下表 M1 保留为**复核清单**，用于核对实现与 PRD 的差距（例：PRD F1.4 要求滑窗 5 次/**5 分钟**，现实现为 5 次/**60 秒**，需按 PRD 对齐）。

---

## 1. M1：骨架 + 认证（D1–D10）

| Day | 任务 | 产出物 | 完成标志 |
|---|---|---|---|
| D1 | 仓库骨架：monorepo、`docker-compose.yml`、`Makefile`、`deploy/Caddyfile`、CI 工作流 | 可 `make up` 起 api/pg/redis/web/caddy | compose config 校验通过 |
| D2 | FastAPI 应用工厂 + `core/config`(pydantic-settings) + `db`(异步引擎/Base/get_db) | 应用启动、DB 连接池就绪 | `/healthz` 返回 200 |
| D3 | 模型 `users`/`roles` + Alembic 初始迁移 + `make seed` 种子账号 | 迁移 upgrade/downgrade 双向可用 | 种子 admin/user 落库 |
| D4 | 安全模块：bcrypt 加盐、JWT 签发/验签（access 30m + refresh 7d） | `core/security.py` | 单测：签发→验签→过期失败 |
| D5 | Redis 客户端 + 登录限流（ZSET 滑动窗口） | `core/rate_limit.py` | 连点第 6 次返回 429 + retry_after |
| D6 | JWT 黑名单 + 登出/刷新轮换 | `core/blacklist.py`、refresh 轮换 | 登出后旧 token 401；旧 refresh 重放失效 |
| D7 | auth 路由（login/refresh/logout/me）+ RBAC 依赖注入 `require_role` | `routers/auth.py`、`deps` | user 访问管理接口 403（非 401） |
| D8 | F8.3 健康检查（API+DB+Redis 探活）+ 统一错误/响应格式 | `/healthz`（Redis 断开→503） | 断 Redis 探活 503 |
| D9 | pytest：登录/限流/黑名单/RBAC（SQLite + fakeredis） | `tests/` 全绿 | 24 passed，无需外部依赖 |
| D10 | 前端登录页 + layout 壳 + openapi-typescript 生成 client | `/login` 可登录跳转 `/dashboard` | 浏览器实测登录 200、`me` 200 |

**M1 收口**：对照 PRD F1.4 限流窗口（5min）修正；确认 F1.5 越权 403 用例入库。

---

## 2. M2：资源 / 配额 / 作业 / 计量（D11–D25）

| Day | 任务 | 产出物 | 完成标志 |
|---|---|---|---|
| D11 | F2.1 节点模型（`nodes`：hostname/CPU/GPU/内存/状态/tags JSONB）+ 迁移；F2.2 节点 CRUD（admin） | 节点表 + `/api/v1/nodes` 增删改查 | 表结构符合 ERD |
| D12 | F2.2 上下线（online/offline/draining）；F2.3 资源视图（用户只读 + 实时空闲卡数） | 下线/上线接口 + 只读列表接口 | 下线后新作业不分配到该节点 |
| D13 | F3.1 配额模型（`quotas`）+ `groups` 模型与迁移；F8.2 组模型基础 | 组 × 资源维度配额表 | 一组一条配额记录 |
| D14 | **F3.2 配额扣减（防超卖核心）**：`UPDATE quotas SET used=used+? WHERE id=? AND limit-used>=? RETURNING` | `services/quota.py` + 提交时同事务预扣 | rowcount=0 即拒绝（409） |
| D15 | **F3.2 并发测试**：`asyncio.gather` 20 并发提交，断言总预扣 ≤ 限额 | `tests/test_quota_concurrency.py` | 20 并发无一超扣，入 CI 常驻 |
| D16 | F3.3 配额返还（终态按未消耗返还）；F3.4 配额管理（admin 调整）；F3.5 配额查询（本组余量） | 返还逻辑 + `/quotas/me`、`/quotas/{group_id}` | 取消运行中作业：已消耗照扣、剩余返还 |
| D17 | F4.1 作业模型（`jobs` 状态机、params JSONB、排队序号）+ 索引 + 迁移 | 状态机 CHECK + `(status,queued_at)`/`(user_id,created_at)` 索引 | 状态流转受 CHECK 约束 |
| D18 | F4.2 作业提交：Pydantic 校验(`Field(le=...)`) → 配额预扣 → 入队（同事务） | `POST /api/v1/jobs` | 超上限 422（字段级）；配额不足 409 |
| D19 | F4.4 取消作业（`UPDATE ... WHERE status IN ('Queued','Running') RETURNING`）；F4.5 列表（分页/筛选/权限） | `/jobs/{id}/cancel`、`GET /jobs` | 终态取消 409；user 仅见自己 |
| D20 | F4.6 作业详情（状态/资源/起止/消耗卡时/日志 mock） | `GET /jobs/{id}` | 详情字段齐全，仅本人/admin |
| D21 | F4.3 模拟调度器：worker 扫描 Queued → 按空闲+配额+FIFO 分配 → Running（记 start_time） | ARQ/APScheduler worker | 提交后秒级进 Running |
| D22 | F4.3 结束流转（walltime/故障率 → Failed/Completed）+ 写 `usage_events` | 调度器计费落库 | 结束自动产生流水 |
| D23 | F5.1 流水模型（`usage_events` 只增不改、reversal 冲正）；F5.2 计费规则（卡时=GPU×分钟向上取整，费率快照） | 流水表 + 计费函数 | 手算 3 case 与系统一致 |
| D24 | F5.3 个人用量（按时间段查明细）；F5.4 组用量排行 | `/usage/me`、`/usage/groups/{id}` | 明细合计 = 汇总值 |
| D25 | M2 前端：作业列表/提交/详情、节点列表、用量页；前后端联调回归 | `/jobs`、`/jobs/new`、`/jobs/[id]`、`/nodes`、`/usage` | 全链路跑通 + 20 并发测试绿 |

**M2 收口**：F3.2 并发用例 + F4 状态机乐观锁用例进 CI；禁止读-改-写。

---

## 3. M3：SSE 实时 + 管理页（D26–D35）

| Day | 任务 | 产出物 | 完成标志 |
|---|---|---|---|
| D26 | F4.7 后端：作业状态变更经 Redis Pub/Sub 扇出 + `GET /jobs/stream`(SSE) | SSE 端点 + 发布点 | 单浏览器可收到推送 |
| D27 | F4.7 前端：SSE 接入 + 收到事件即 `invalidateQueries` | 作业列表实时刷新 | 无手动刷新即更新 |
| D28 | F4.7 双浏览器验收；F2.3 节点列表页（只读表格 + GPU 占用条） | `/nodes` 页面 | 两浏览器 ≤2s 同步 |
| D29 | 管理-节点页 `/admin/nodes`（F2.2：CRUD + 上下线） | 管理节点页 | 下线后不再接收新作业 |
| D30 | 管理-配额页 `/admin/quotas`（F3.4：组配额编辑） | 管理配额页 | 调低配额不杀在跑作业 |
| D31 | F8.1 用户管理：admin 建用户/分配角色/重置密码 + `/admin/users` 页 | 用户管理接口 + 页面 | 新用户可登录且角色生效 |
| D32 | F8.2 用户组管理：组增删改/成员维护 + `/admin/groups` 页 | 组管理接口 + 页面 | 绑配额后组不可硬删 |
| D33 | 前端统一：shadcn/ui + TanStack Query 规范、Data Table/状态徽标/表单校验同规则 | 组件与样式统一 | 视觉/交互一致性通过 |
| D34 | M2+M3 联调回归：越权、边界、状态机、配额边界用例 | 回归清单 | 关键用例全绿 |
| D35 | M3 验收 + 缓冲 | 验收记录 | 双浏览器秒级刷新达成 |

---

## 4. M4：报表 + 审计 + 上线（D36–D40）

| Day | 任务 | 产出物 | 完成标志 |
|---|---|---|---|
| D36 | F6.1 月结物化视图 `mv_monthly_billing`（月×用户×组×卡时）+ 定时 `REFRESH CONCURRENTLY` | 物化视图 + 定时任务 | 流水插入 ≤1h 报表可见，刷新不锁表 |
| D37 | F6.2 报表接口 `/reports/monthly`、`/reports/dashboard` | 报表聚合接口 | 数据与流水对账一致 |
| D38 | F7.1 审计触发器（users/quotas/nodes/jobs 的 UPDATE/DELETE→`audit_log`）；F7.2 操作人注入中间件 `SET LOCAL app.user`；F7.3 审计查询接口 | 触发器 + 中间件 + `/audit-logs` | 改字段即留痕，操作人=登录人 |
| D39 | F6.2 前端看板（ECharts：卡时趋势/队列占用/热力图/TOP）；F7.3 前端审计页 `/admin/audit` | `/admin/reports`、`/admin/audit` 页 | 看板加载 <2s；审计筛选可用 |
| D40 | F6.3 慢查询优化（复合索引/改写）+ 种子 1 万作业 + 压测 p95<200ms；README、metrics.md、demo 视频、上线 | 优化前后对比 + demo + 线上地址 | metrics.md 有前后数字；线上可访问 |

**M4 收口**：非功能需求核对——并发正确性(CI)、性能(p95<200ms)、安全、审计 100% 覆盖、可观测、一键部署。

---

## 5. 关键路径与依赖

```
D11 节点模型 ─┐
D13 组/配额模型 ─┴─> D14 配额扣减(防超卖) ─> D18 作业提交 ─> D21/22 调度器+计费
                                                    │
D23 流水模型 ───────────────────────────────────────┘
                                                    │
D26 SSE ─> D27 前端实时          D36 物化视图 ─> D37 报表接口 ─> D39 看板
D38 审计触发器/中间件 ─> D39 审计页 ─┐
D17 作业模型 ───────────────────────┴─> D19/D20 列表/详情 ─> D25 前端联调
```

- **不可砍（业务立身）**：F3.2 防超卖、F5 计费流水、F7.1 审计。
- **可延期先砍**：F4.6 日志、F6.2 热力图、分片上传。

---

## 6. 每日 DoD（完成定义）

每条任务收口须满足：

1. 代码合入 + 迁移（若有）upgrade/downgrade 双向可用；
2. 对应验收标准（PRD 表格）逐条自测通过；
3. 新增逻辑有 pytest 覆盖（并发/状态机类必测）；
4. `ruff check/format`、`next build` 全绿；
5. 涉及指标的场景，实测数字写入 `metrics.md`，未测保持占位、不得编造。

---

## 7. 风险与缓冲

| 风险 | 影响 | 应对 |
|---|---|---|
| 配额并发正确性 | 超卖 → 业务不可信 | D15 用例先行、CI 常驻；必要时 PG 行锁兜底 |
| 调度器多实例抢占 | 重复调度 | 单实例先行；多实例用 PG advisory lock（D22 备注） |
| 物化视图刷新锁表 | 报表阻塞业务 | 强约束用 `REFRESH CONCURRENTLY` |
| M4 单周偏紧 | 报表/审计/上线挤在 5 天 | D35 缓冲压到 M3 联调；F6.2 热力图列为可砍项 |

---

## 8. 进度记录（实际执行）

> 逐日回填：完成日 / 实际产出 / 证据 / 与计划的差异。未实测的数字不写。

### D11–D12 · F2 节点域 —— 已完成

- **产出**：`app/models/node.py`、`app/schemas/node.py`、`app/services/node_service.py`、`app/routers/nodes.py`、`alembic/versions/0002_nodes.py`、`tests/test_nodes.py`
- **接口**：`GET /nodes`、`GET /nodes/{id}`（登录可读，F2.3 只读资源视图，含 `gpu_free`）；`POST/PATCH/DELETE /nodes`（admin）
- **证据**：pytest **34 passed**；ruff check/format 全过；`upgrade → downgrade base → upgrade` 在 SQLite 双向通过，`nodes` 表含 2 条 CHECK（status 枚举、`gpu_used<=gpu_total`）+ 2 个索引
- **差异**：PRD 写 `PUT /nodes`，实现沿用项目既有约定 `PATCH`（与 `users.py` 一致）

### D13–D15 · F3.1 / F3.2 配额模型 + 防超卖核心 —— 已完成

- **产出**：`app/models/group.py`、`app/models/quota.py`、`app/db/types.py`（共享 `JSONDict`）、`app/db/base.py`（`user_groups`）、`alembic/versions/0003_groups_quotas.py`、`app/services/quota_service.py`、`tests/test_quota.py`；`app/initial_data.py` 种子新增 `default` 组 + 配额（gpu_limit=1000）
- **证据**：pytest **43 passed**；**20 并发预扣恰好 10 次成功、`gpu_used=100` 且 `<= gpu_limit`，无超卖**；ruff 全过；迁移 `0002 → head → base → head` 通过
- **差异 1**：D14（扣减）与 D15（并发测试）合并一次交付
- **差异 2**：并发用例改用**文件库多连接**（conftest 的 in-memory SQLite 用 `StaticPool` 单连接，会串行化，测不出竞态）
- **实现要点**：条件原子更新 `UPDATE quotas SET gpu_used = gpu_used + :n WHERE group_id = :g AND gpu_used + :n <= gpu_limit` + `rowcount` 判定；未采用 `SELECT ... FOR UPDATE` 或 `RETURNING`（前者需额外事务管理，后者跨库兼容性略差），DB 层 CHECK 约束兜底；`reserve` / `release` **不 commit**，由调用方保证「预扣 + 入队」同事务

### D16 · F3.3 / F3.4 / F3.5 配额返还 + 管理 + 查询 —— 已完成

- **产出**：`app/schemas/quota.py`、`app/routers/quotas.py`、`quota_service.list_user_quotas` / `update_quota`、`tests/test_quota_endpoints.py`
- **接口**：`GET /quotas/me`（登录，我所属各 active 组余量）；`GET /quotas/{group_id}`、`PATCH /quotas/{group_id}`（admin）
- **证据**：pytest **52 passed**；ruff 全过；OpenAPI 已生成 3 条新路径
- **差异**：PRD 写 `PUT`，实现用 `PATCH`；新增规则 `gpu_limit` 不得低于已用 `gpu_used`（否则 409），保证 CHECK 不被应用层击穿

### 计划偏差清单（待统一口径）

| 项 | PRD | 当前实现 | 处置建议 |
|---|---|---|---|
| 节点/配额更新方法 | `PUT /nodes`、`PUT /quotas/{id}` | `PATCH`（与 `users` 一致） | 先保持内部一致，或统一改 `PUT` |
| 健康检查路径 | `/healthz`（F8.3） | `/health`、`/health/ready` | 归入 M1 收口时对齐 |
| 登录限流窗口 | 5 次 / **5 分钟**（F1.4） | 5 次 / **60 秒** | 归入 M1 收口时按 PRD 调整 |

### D17 · F4.1 作业模型 + 状态机 + 索引 + 迁移 —— 已完成

- **产出**：`app/models/job.py`（`Job` + `JobStatus` + `ALLOWED_TRANSITIONS` + `TERMINAL_STATUSES`）、`alembic/versions/0004_jobs.py`、`tests/test_job_model.py`
- **表结构**：`user_id`/`group_id`（**不设 ON DELETE CASCADE**，作业是对账/审计证据必须留存）、`node_id`（`ON DELETE SET NULL`，删节点不影响账单真相）、`num_nodes`/`num_gpus`/`walltime_min`/`params` JSONB/`status`/`queued_at`/`started_at`/`ended_at`
- **约束与索引**：CHECK `status IN (5 态)`、`num_nodes>0 AND num_gpus>=0`、`walltime_min>0`；索引 `(status, queued_at)`（喂调度器扫描）、`(user_id, created_at)`（喂用户作业历史）
- **证据**：pytest **58 passed**；ruff 全过；迁移 `head → 0003 → head → base → head` 双向通过；`jobs` 含 3 条 CHECK + 4 个索引
- **设计取舍**：「排队序号」不建列，按 `ORDER BY (queued_at, id)` 计算，避免额外的原子自增计数器；状态机用 `UPDATE ... WHERE status IN (...)` 乐观检查（D19 落地），禁止读-改-写

### D18 · F4.2 作业提交（校验 → 配额预扣 → 入队同事务）—— 已完成

- **产出**：`app/schemas/job.py`（`JobCreate`/`JobRead`/`JobList`）、`app/services/job_service.py`、`app/routers/jobs.py`、`tests/test_job_submit.py`；`app/core/errors.py` 新增 `UnprocessableEntityError`（422，携带字段级 `errors`，与 FastAPI 校验体同形）
- **接口**：`POST /jobs`（登录，返回 201）
- **执行顺序**：① 校验组存在且 active + 调用者是成员（`user_groups`，非成员 403、组不存在 404）→ ② 拉组配额，校验 `num_nodes ≤ max_nodes_per_job`、`num_gpus ≤ max_gpus_per_job`（越限 **422 字段级**）→ ③ `quota_service.reserve_or_raise`（原子条件更新，越限 **409**）→ ④ 建 `queued` 作业；**③④ 同一事务**（`reserve` 不 commit，`db.commit()` 一次性提交预扣 + 入队）
- **证据**：pytest **67 passed**（新增 9 例）；ruff check/format 全过；无新迁移（复用 `0004_jobs`）
- **用例**：匿名 401；成功 201 + `status=queued` + 配额 `gpu_used` 递增；`num_gpus=0`（CPU-only）不动配额；超组上限 422（断言 `loc=["body","num_gpus"]`/`["body","num_nodes"]`）；`walltime_min` 超静态上限 422；配额不足 409 且 **`gpu_used` 回滚、jobs 表 0 行**（原子性）；非成员 403；未知组 404
- **设计取舍**：静态上限（`Field(le=…)`，如 `num_gpus ≤ 4096`、`walltime_min ≤ 一周`）与组级上限（依赖配额、动态）分两层校验——前者在 schema，后者在 service 抛 422 同形错误体，保证「超上限字段返回 422 及明确字段错误」这条验收同时覆盖两种来源

### D19 · F4.4 取消作业 + F4.5 作业列表 —— 已完成

- **产出**：`job_service.list_jobs` / `get_job` / `cancel_job`、`app/models/job.py` 新增 `CANCELABLE_STATUSES`、`app/routers/jobs.py` 新增 `GET /jobs` 与 `POST /jobs/{id}/cancel`、`tests/test_job_cancel_list.py`
- **取消（F4.4）**：`UPDATE jobs SET status='canceled', ended_at=now() WHERE id=? AND status IN ('queued','running')` 单语句完成「状态检查 + 写入」，`rowcount != 1` → **409**（已终态/并发取消的败者都不会重复返还）；权限为**本人或 admin**（他人 403、不存在 404）；取消成功后按 `num_gpus` **返还配额预扣**（实际消耗的卡时由 D22 的 `usage_events` 计费，不走配额池）
- **列表（F4.5）**：分页 `skip/limit`（limit ≤ 200）+ `status` / `since` / `until`（按 `queued_at`）筛选；**非 admin 强制 `user_id = 当前用户`**，admin 看全部；排序 `(queued_at DESC, id DESC)`；返回 `{items, total}`
- **证据**：pytest **81 passed**（新增 14 例）；ruff check/format 全过
- **用例**：本人取消 200 且配额回 0；重复取消 409 且不二次返还；已完成作业取消 409；admin 可取消他人作业；非本人非 admin 403；未知作业 404；匿名 401；用户仅见自己 / admin 见全部；状态筛选；分页 `total` 与不重叠；时间窗口（未来/过去/覆盖）筛选；非法 status 参数 422
- **设计取舍**：列表按 `queued_at` 过滤而非 `created_at`——同一语义更贴近「排队时间」，且与 `ix_jobs_status_queued_at` 索引对齐；取消不做读-改-写，避免两个请求都判定可取消

### D20 · F4.6 作业详情 —— 已完成

- **产出**：`app/schemas/job.py` 新增 `JobDetail`（继承 `JobRead`，追加 `gpu_minutes` / `log`）、`job_service.get_visible_job` / `used_gpu_minutes` / `build_log`、`GET /jobs/{id}`、`tests/test_job_detail.py`
- **详情字段**：状态、节点数/卡数、walltime、params、`queued_at`/`started_at`/`ended_at`、**消耗卡时 `gpu_minutes`**、**mock 日志 `log`**
- **卡时口径**：`gpu_minutes = num_gpus × ceil(elapsed_seconds / 60)`，与 F5.2 计费规则（按分钟向上取整）一致；未启动为 0，运行中按「到当前时刻」累计，结束后按 `ended_at` 固定
- **权限**：仅本人或 admin（`get_visible_job`，他人 403、不存在 404、匿名 401）；取消接口复用同一权限函数
- **证据**：pytest **88 passed**（新增 7 例）；ruff check/format 全过
- **用例**：匿名 401；本人读排队中作业（`gpu_minutes=0`、日志含 `[queued]`）；已完成作业 `2 卡 × 30 分钟 = 60` 且日志含 queued/running/completed；运行中按当前时刻累计；admin 可读他人；非本人 403；未知 404
- **实现要点**：SQLite 读回的是 naive datetime，统一 `_as_utc()` 归一后再做差值，避免 `naive - aware` 报错（PG 下为 aware，同样兼容）

### D21 · F4.3 模拟调度器（排队 → Running）—— 已完成

- **产出**：`app/services/scheduler_service.py`（`schedule_once` / `_claim_node`）、`app/workers/tasks.py`（`schedule_jobs` ARQ 任务）、`app/workers/settings.py`（启动轮询循环）、`core/config.py` 新增 `scheduler_interval_seconds`（默认 2s）、`tests/test_scheduler.py`
- **调度逻辑**：扫描 `status='queued'` 按 `(queued_at, id)` **FIFO**；候选节点为 `status='online'` 且 `gpu_total - gpu_used >= num_gpus`，按 `gpu_used ASC, hostname ASC` **优先最空闲**；用条件 `UPDATE nodes SET gpu_used = gpu_used + :n WHERE ... AND gpu_used + :n <= gpu_total` 原子占卡（与配额同一防超卖模式）；再条件 `UPDATE jobs SET status='running', node_id=?, started_at=now() WHERE id=? AND status='queued'`，若已被别的实例抢占则回滚并释放节点卡
- **事务粒度**：每个作业独立事务（节点占卡 + 状态流转一起提交），单个作业不可调度不会阻塞后续队列；无可调度节点时 job 保持 `queued`
- **运行时**：ARQ worker（`arq app.workers.settings.WorkerSettings`）在 `on_startup` 启动 2s 轮询循环，`on_shutdown` 取消；也可把 `schedule_jobs` 当普通任务入队
- **证据**：pytest **96 passed**（新增 8 例）；ruff check/format 全过；worker 配置导入校验通过（`functions=[ping, schedule_jobs]`）
- **用例**：有容量即启动并占卡；容量不足保持 queued；offline/draining 节点跳过；FIFO 先到先得；优先最空闲节点；节点被填满后第 3 个作业继续排队；二次调度不重复启动（`started_at`/`gpu_used` 不变）；`num_gpus=0` 的 CPU-only 作业可落节点且不占卡
- **计划偏差**：MVP 每个作业只落到**单个节点**（`jobs` 仅有一个 `node_id`），`num_nodes` 先作为请求参数保留；D22 结束时可按该 `node_id` 精确释放节点卡

### D22 · F4.3 结束流转 + usage_events —— 已完成

- **产出**：`app/models/usage_event.py`、`alembic/versions/0005_usage_events.py`、`scheduler_service.finish_due_jobs`、worker 每轮执行「启动 + 结束」两趟、`core/config.py` 新增 `scheduler_failure_rate`（默认 0）、`tests/test_job_finish.py`
- **结束规则**：扫描 `running` 作业，`duration_min = ceil(elapsed_seconds / 60)`；`duration_min >= walltime_min` → `completed`；或命中失败率 → `failed`；终态写 `ended_at`
- **资源与流水（同一事务）**：条件更新 `jobs WHERE status='running'`（`rowcount != 1` 回滚跳过，防重复结束）→ 释放 `node.gpu_used`（`WHERE gpu_used >= n` 钳位不为负）→ 追加一条 `usage_events`（`gpus` / `started_at` / `ended_at` / `duration_min` / `gpu_hours` / `rate_snapshot` / `reversed`）
- **计费口径**：`gpu_hours = gpus × duration_min / 60`（保留 4 位，四舍五入），与 F4.6 的 `gpu_minutes` 同源，保证详情页与流水对账一致
- **证据**：pytest **103 passed**（新增 7 例）；ruff check/format 全过；迁移 `base → head → base → head` 双向通过，`usage_events` 含 3 条 CHECK + 3 个索引
- **用例**：walltime 到期 → completed + 释放节点 + 流水（2 卡 × 61 分钟 = `2.0333` 卡时）；未到期不动；恰好到期；注入失败 → failed + 流水（`0.1667`）；二次结束不重复产生流水；queued/终态忽略；多作业结束流水条数一致
- **计划偏差**：`usage_events` 模型本应在 D23 建，因 D22 需要「结束即落流水」而**提前到 D22**；D23 继续完成 F5.2 费率快照填充、冲正（`reversed`）与流水查询
- **设计取舍**：`scheduler_failure_rate` 默认 **0**（2s 轮询下非零故障率会过快杀作业，不符合演示预期）；失败路径通过配置 + 注入 `rng` 在测试中确定性验证

### D23 · F5.1 冲正 + F5.2 费率快照与计费规则 —— 已完成

- **产出**：`app/services/billing_service.py`（`gpu_hours` / `rate_snapshot` / `net_gpu_hours` / `reverse_job_usage`）、`core/config.py` 新增 `billing_currency` 与 `gpu_hour_rates`（按 `gpu_model` 定价，`default` 兜底）、`scheduler_service.finish_due_jobs` 改为落费率快照、`tests/test_billing.py`
- **计费规则（F5.2）**：`duration_min = ceil(elapsed/60)`，`gpu_hours = gpus × duration_min / 60`（4 位小数，四舍五入）；手算 3 个 case 与系统一致：`1 卡×1 分钟=0.0167`、`2 卡×61 分钟=2.0333`、`4 卡×30 分钟=2.0000`
- **费率快照**：按作业所在节点的 `gpu_model` 取单价，写入 `rate_snapshot`（`gpu_model` / `rate_key` / `gpu_hour` / `currency`，金额以字符串存避免 JSON 浮点误差）；未知型号回退 `default`；改价不影响历史流水
- **冲正（F5.1）**：原流水**永不修改/删除**；`reverse_job_usage(job_id)` 追加 `reversed=true` 的补偿行（金额与时长同原行，可带 `reversal_reason`）；`net_gpu_hours` 汇总时**减法**处理补偿行；同一作业重复冲正为幂等空操作
- **证据**：pytest **111 passed**（新增 8 例）；ruff check/format 全过；无新迁移（复用 `0005_usage_events`）
- **用例**：3 组手算卡时；已知/未知型号与缺省费率快照；A100 节点结束落 `30.0000` 费率快照；冲正追加补偿行且净卡时为 0；原行保持不变；重复冲正幂等；无流水冲正为空操作

### 下一步

- D24 F5.3 个人用量（按时间段查明细，明细合计 = 汇总）+ F5.4 组用量排行

