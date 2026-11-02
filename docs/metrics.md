# 项目① HPC 平台 · 量化指标（实测）

> 更新口径：第 3 周（按计划第 1 周 2026-10-12 起算）
> 原则：只写实测值；未测项保持「待测」，不估算、不编造。
> 数据来源：本地 `pytest` / `next build` / `scripts/demo_scheduler.py` 输出。

## 1. 汇总

| 指标 | 目标 | 实测 | 说明 |
|---|---|---|---|
| 单元测试 | 全绿 | **153 passed**（≈14.5s） | SQLite + fakeredis，无需外部依赖 |
| 后端规范 | 0 error | **ruff check / format 全过** | 74 个文件（不含迁移） |
| 配额并发防超卖 | 20 并发不超扣 | **恰好 10 次成功，`gpu_used=100 ≤ gpu_limit=100`** | 文件库多连接真并发 |
| 作业提交原子性 | 配额不足不落库 | **409 后 `gpu_used` 回滚、`jobs` 表 0 行** | 预扣 + 入队同事务 |
| 调度并发不超分 | 多调度器不重复占位 | **两路并发 20 作业：10 running / 10 queued** | 条件 `UPDATE + rowcount` |
| 100 作业 demo | 全终态、无泄漏 | **100 completed / 14 轮 / 节点 gpu=cpu=mem=0 / quota=0** | 4 节点 × 8 卡，每作业 2 卡+8 核+32G |
| 结束自动计费 | 流水与详情一致 | **2 卡 × 61 分钟 = `2.0333` GPU·h** | 按分钟向上取整 |
| 冲正对账 | 冲正后净卡时为 0 | **原行保留 + 补偿行，`net_gpu_hours=0`** | 只增不改 |
| 用量对账 | 明细合计 = 汇总 | **`sum(signed_gpu_hours) == total_gpu_hours`** | 含冲正场景 |
| 策略切换 | 一行配置 | **`SCHEDULER_POLICY=fifo\|priority`** | 首批顺序不同（见 §2.2） |
| 前端构建 | 成功 | **typecheck + lint + build 全过，14 条路由** | Next 14.2.15 |
| 前端样式收敛 | 无重复硬编码 | **9 个页面复用 3 个 UI 模块；旧表格/输入框/徽标类名字面量清零** | `components/ui/*` |
| 用户管理 | 重置密码即刻生效 | **旧密码 401 / 新密码 200；改角色无需重新登录即生效** | `PATCH /users/{id}` |
| 资源组管理 | 停用不破历史、不误停占卡组 | **7 条用例：重名 409、未知成员 404、成员覆盖幂等、`gpu_used>0` 停用 409、软删除后仍可查** | `DELETE` 仅置 `is_active=False` |
| 前端首屏 JS | — | **shared 87.2 kB；/jobs 99.2 kB；/admin/nodes 99.6 kB** | `next build` |
| 迁移可用性 | 双向 | **`base → head → base → head` 通过（0001–0006）** | SQLite 验证 |

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

## 3. 待测 / 未验证

| 项 | 状态 | 原因 |
|---|---|---|
| 双浏览器 SSE ≤2s 同步 | 待测 | Docker 未运行；本机 Redis 需认证，无法起完整栈 |
| 登录接口 P95 延迟 | 待测 | 尚未接入压测 |
| 测试覆盖率 | 待测 | 尚未接入 pytest-cov |
| 前端浏览器 E2E | 待测 | 同上，需 PG/Redis/worker/web 全栈 |
| 1 万作业压测 p95 | 待测 | M4 D40 范围 |
