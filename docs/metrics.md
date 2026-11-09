# 项目① HPC 平台 · 量化指标（实测）

> 更新口径：第 3 周（按计划第 1 周 2026-10-12 起算）
> 原则：只写实测值；未测项保持「待测」，不估算、不编造。
> 数据来源：本地 `pytest` / `next build` / `scripts/demo_scheduler.py` 输出。

## 1. 汇总

| 指标 | 目标 | 实测 | 说明 |
|---|---|---|---|
| 单元测试 | 全绿 | **168 passed**（≈20.9s） | SQLite + fakeredis，无需外部依赖 |
| 测试覆盖率 | ≥ 80% | **80.09%**（1924 语句 / 383 未覆盖） | `make cov`（`--cov-fail-under=80`） |
| 后端规范 | 0 error | **ruff check / format 全过** | 74 个文件（不含迁移） |
| 配额并发防超卖 | 20 并发不超扣 | **恰好 10 次成功，`gpu_used=100 ≤ gpu_limit=100`** | 文件库多连接真并发 |
| 作业提交原子性 | 配额不足不落库 | **409 后 `gpu_used` 回滚、`jobs` 表 0 行** | 预扣 + 入队同事务 |
| 调度并发不超分 | 多调度器不重复占位 | **两路并发 20 作业：10 running / 10 queued** | 条件 `UPDATE + rowcount` |
| 100 作业 demo | 全终态、无泄漏 | **100 completed / 14 轮 / 节点 gpu=cpu=mem=0 / quota=0** | 4 节点 × 8 卡，每作业 2 卡+8 核+32G |
| 结束自动计费 | 流水与详情一致 | **2 卡 × 61 分钟 = `2.0333` GPU·h** | 按分钟向上取整 |
| 冲正对账 | 冲正后净卡时为 0 | **原行保留 + 补偿行，`net_gpu_hours=0`** | 只增不改 |
| 用量对账 | 明细合计 = 汇总 | **`sum(signed_gpu_hours) == total_gpu_hours`** | 含冲正场景 |
| 策略切换 | 一行配置 | **`SCHEDULER_POLICY=fifo\|priority`** | 首批顺序不同（见 §2.2） |
| 前端构建 | 成功 | **typecheck + lint + build 全过，16 条路由** | Next 14.2.15 |
| 前端样式收敛 | 无重复硬编码 | **11 个页面复用 3 个 UI 模块；旧表格/输入框/徽标类名字面量清零** | `components/ui/*` |
| 用户管理 | 重置密码即刻生效 | **旧密码 401 / 新密码 200；改角色无需重新登录即生效** | `PATCH /users/{id}` |
| 资源组管理 | 停用不破历史、不误停占卡组 | **7 条用例：重名 409、未知成员 404、成员覆盖幂等、`gpu_used>0` 停用 409、软删除后仍可查** | `DELETE` 仅置 `is_active=False` |
| 全链路回归 | 提交→调度→结束→计费一致 | **详情 `gpu_minutes=120` 与流水 `duration_min=60` 同源；配额与节点三计数器归零** | `tests/test_regression.py` |
| 取消运行中作业 | 三类槽位全部返还 | **GPU/CPU/内存均归零、无流水、重复取消 409** | 回归用例 2 |
| 边界校验 | 越界一律 422 | **提交 8 类越界 + 查询 7 类非法参数逐项断言** | 回归用例 3/4 |
| M3 验收 | 验收清单闭环 | **9 项中 8 项通过；D28 双浏览器 ≤2s 实测待环境** | 开发计划 D35 验收表 |
| 月结报表 | 聚合口径与流水一致 | **2 月/1 月共 2 个 bucket，冲正折负后合计 `6.5000` 卡时；`job_count` 去重** | `tests/test_reports.py` |
| 审计留痕 | 改字段即留痕、密码不外泄 | **节点更新/删除各 1 条；`before/after` 无 `hashed_password`；作业仅 `status` 变化留痕** | `tests/test_audit.py` |
| 平台看板 | 数据与报表同口径 | **6 个自然月补零、TOP 排序正确、非 admin 403** | `GET /reports/dashboard` |
| 读路径压测（1 万作业） | p95 < 200ms | **6/6 通过，最慢为看板 34.95ms** | SQLite，10 轮，见 §2.4 |
| 登录接口延迟 | p95 < 200ms | **串行 p95 108.3ms / 并发 10 p95 319.0ms；吞吐 39 logins/s** | bcrypt 10 轮，见 §2.5 |
| SSE 端到端（HTTP 层） | ≤2s | **122.4ms**（提交 → 订阅端收到事件） | `scripts/dev_stack.py`，见 §2.6 |
| 前端首屏 JS | — | **shared 87.2 kB；/jobs 99.4 kB；/dashboard 92.2 kB** | `next build` |
| 迁移可用性 | 双向 | **`base → head → base → head` 通过（0001–0008）** | SQLite 验证；0007/0008 非对应方言降级为 no-op |

## 2. 关键证据

### 2.1 配额防超卖（20 并发）

用例：`tests/test_quota.py::test_concurrent_reservations_never_oversell`

- 场景：`gpu_limit=100`，20 个协程各申请 10 卡，`asyncio.gather` 真并发（临时文件库 + 独立连接）
- 结果：**恰好 10 次成功**，`gpu_used=100`，其余 10 次返回 `False`
- 实现：`UPDATE quotas SET gpu_used = gpu_used + :n WHERE group_id = :g AND gpu_used + :n <= gpu_limit`，以 `rowcount` 判定
- 兜底：`quotas.group_id` 唯一约束 + `CHECK (gpu_used >= 0 AND gpu_used <= gpu_limit)`
- 与计划的差异：没有使用 `with_for_update()` 行锁。条件原子更新把「检查 + 扣减」压成一条语句，避免读-改-写竞态，且不依赖数据库的锁语义（SQLite/PG 均可验证）

### 2.2 100 作业调度 demo

命令：`cd apps/api && python scripts/demo_scheduler.py --compare`

| 策略 | 轮次 | 终态 | 节点残留 (gpu/cpu/mem) | 配额残留 | 流水 | 首批 10 个作业 |
|---|---|---|---|---|---|---|
| FIFO | 14 | 100 completed | 0 / 0 / 0 | 0 | 100 条（6.6700 GPU·h） | `[1,2,3,4,5,6,7,8,9,10]` |
| Priority | 14 | 100 completed | 0 / 0 / 0 | 0 | 100 条（6.6700 GPU·h） | `[28,55,82,9,36,63,90,17,44,71]` |

> demo 在 SQLite 上自建临时库，不需要 PG/Redis；断言全终态、节点三 counters 归零、配额归零、流水条数 = 作业数。

### 2.3 调度并发与崩溃回收

- 并发调度：`test_concurrent_scheduler_passes_never_over_allocate`，两路调度同时抢 10 个 GPU 槽 → 10 running / 10 queued
- 崩溃回收：`tests/test_orphan_recovery.py`
  - 租约过期 → `failed`，节点 GPU/CPU/内存归零，仍写 10 分钟流水
  - 未过期租约不被误杀，续租成功
  - 重复回收幂等（只产生 1 条流水）
  - 历史无租约数据自动收编

### 2.4 读路径压测（1 万作业）

命令：`cd apps/api && python scripts/bench_queries.py --jobs 10000 --runs 10`

| 查询 | p50 | p95 | max |
|---|---|---|---|
| 作业列表（用户 + 分页 50） | 4.19 ms | 5.58 ms | 7.52 ms |
| 作业列表（状态 + 分页 50） | 4.13 ms | 4.69 ms | 5.38 ms |
| 个人用量明细 | 19.46 ms | 23.63 ms | 77.30 ms |
| 组用量排行 | 18.02 ms | 20.34 ms | 27.37 ms |
| 月结聚合（按组） | 7.23 ms | 8.28 ms | 8.38 ms |
| 平台看板（totals/趋势/TOP） | 32.13 ms | 34.95 ms | 38.51 ms |

- **6/6 p95 < 200 ms**（SQLite，单进程无网络开销，是 PostgreSQL 的下界参考）
- `EXPLAIN QUERY PLAN` 确认作业列表走 `ix_jobs_user_created_at`
- 改写：看板原先聚合两次（趋势窗口 + 本月），合并为一次后按月份分流

### 2.5 登录延迟（bcrypt + 事件循环）

命令：`cd apps/api && python scripts/bench_login.py [--rounds 10|12]`

| 配置 | 串行 p50 / p95 | 并发 10 p50 / p95 | 吞吐 |
|---|---|---|---|
| bcrypt 12 轮 | 321.9 / 350.5 ms | 581.0 / 709.8 ms | 16.6 logins/s |
| **bcrypt 10 轮（现默认）** | **95.8 / 108.3 ms** | **232.0 / 319.0 ms** | **39.0 logins/s** |

- 并发/串行 p50 比值 1.8–2.4（若 bcrypt 跑在事件循环上应接近 10）——说明 `asyncio.to_thread` 生效
- 默认 rounds 由 12 调为 10（OWASP 下限），单次登录 p95 从 350ms 降到 108ms，吞吐翻倍
- 代价：哈希强度降低 4 倍；如需更强防护可把 `BCRYPT_ROUNDS` 调回 12，延迟同比翻倍

### 2.6 无 Docker 端到端验证（D28 替代证据）

命令：`cd apps/api && python scripts/dev_stack.py --reset`（SQLite + fakeredis + 进程内调度循环）

- 提交 → 调度 → 完成：`walltime_min=1` 的作业 4s 内进入 `completed`，`gpu_minutes=2`（2 卡 × 1 分钟向上取整），配额与节点槽位归零
- SSE 投递：订阅端（admin）在另一客户端提交后 **122.4ms** 收到 `queued` 事件（本机 HTTP 层，目标 ≤2s）
- **真实浏览器**（`npm run dev` + 浏览器自动化）：登录 admin 成功跳转 `/dashboard`，`/jobs` 渲染出 2 行作业（#1 已完成 / #2 失败）；服务端日志同时记录 `GET /api/v1/jobs/stream 200`，说明前端 SSE 订阅在浏览器里真实建立
- 说明：链路走真实 FastAPI + SSE 流 + 调度服务，只是把 PostgreSQL/Redis 换成了 SQLite/fakeredis；「双窗口同时对照」仍需人工，但两端各自已验证

## 3. 待测 / 未验证

| 项 | 状态 | 原因 |
|---|---|---|
| 双浏览器 SSE ≤2s 同步 | 部分实测 | HTTP 层 122.4ms + 浏览器内 `/jobs/stream` 已建立；双窗口同时对照待人工 |
| 前端浏览器 E2E | 部分实测 | 登录 → `/jobs` 渲染通过；其余管理页未逐页点按 |
| PostgreSQL 侧 p95 | 待测 | 本机无 PG，压测仅在 SQLite 上完成 |

### 3.1 覆盖率结构说明

- 核心业务（`billing_service` / `scheduling_policy` / 全部 `models` / `schemas`）为 96%–100%
- 0% 的两处是胶水代码：`initial_data.py`（种子脚本）、`workers/*`（ARQ 入口与轮询循环）
- 偏低：`node_service` 41%、`user_service` 49%、`group_service` 49%——多为 404/冲突分支，已被对应路由用例间接覆盖主路径，剩余是防御性分支
