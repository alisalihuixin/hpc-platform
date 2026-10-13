# 项目① HPC 作业调度与计费管理平台 · 需求文档（PRD v1.0）

> 版本：v1.0 ｜ 2026-10-13 ｜ 状态：可进入开发
> 定位：算力运营系统的计量结算核心（对标润宇 Unify 平台运营服务 / 并行科技计量计费系统）
> 业务一句话：让算力使用**可计量、可结算、可追溯**——配额管分配、流水管计量、报表管经营。

---

## 1. 业务背景与目标

### 1.1 业务逻辑

超算/智算中心的经营本质是稀缺算力的分配与计量：

```
资源供给（节点/GPU） → 分配权利（配额） → 资源消费（作业） → 计量凭证（卡时流水） → 经营决策（报表/对账）
```

| 业务概念 | 技术实现 | 类比 |
|---|---|---|
| 配额 | quotas 表 + 行锁扣减 | 银行信用额度（不是库存） |
| 作业 | jobs 状态机 | 消费行为 |
| 卡时流水 | usage_events 只增不改 | 银行流水（对账的唯一可信源） |
| 月结报表 | 物化视图 + 定时刷新 | 月结单 |
| 审计日志 | 触发器自动记录 | 合规与追责依据 |

### 1.2 业务目标

1. 用户：在线提交作业、实时看状态、查自己的用量和账单
2. 管理员：管节点、管配额、看经营报表
3. 系统：分配公平（不超卖）、计量准确（可对账）、操作可追溯（审计）

### 1.3 非目标（Out of Scope）

- 不实现真实 Slurm/K8s 对接（用模拟调度器代替，预留 adapter 接口）
- 不做真实支付（充值/账单为内部记账，不对接支付网关）
- 不做多集群纳管（单集群，架构上预留 cluster_id 字段）

---

## 2. 用户角色与权限（RBAC）

| 角色 | 标识 | 核心诉求 |
|---|---|---|
| 普通用户 | `user` | 提交/取消作业、看自己的作业和用量 |
| 组管理员 | `group_admin`（可选，M4 前并入 admin） | 管理本组成员配额 |
| 系统管理员 | `admin` | 节点管理、配额管理、报表、审计查看 |

权限矩阵：

| 功能 | user | admin |
|---|---|---|
| 登录/登出/刷新 token | ✓ | ✓ |
| 提交/取消自己的作业 | ✓ | ✓ |
| 查看自己的用量/账单 | ✓ | ✓ |
| 查看他人作业 | ✗ | ✓ |
| 节点增删改/上下线 | ✗ | ✓ |
| 用户组配额设置 | ✗ | ✓ |
| 全局报表/审计日志 | ✗ | ✓ |

---

## 3. 功能模块总览

```
F1 认证与账号       F2 资源域（节点管理）   F3 配额域
F4 作业域（核心）    F5 计量计费域          F6 报表域
F7 审计域           F8 平台管理（运维支撑）
```

### F1 认证与账号

| 编号 | 功能点 | 需求描述 | 验收标准 |
|---|---|---|---|
| F1.1 | 登录 | 邮箱+密码，bcrypt 校验，返回 access(30min)+refresh(7d) JWT | 正确凭证登录成功；错误凭证 401 且不区分"用户不存在/密码错" |
| F1.2 | 登出 | access token 写入 Redis 黑名单（TTL=剩余有效期），refresh 同时吊销 | 登出后旧 access token 访问任意接口 401 |
| F1.3 | 刷新 | refresh token 换新 access | 旧 refresh 一次性使用，重放失效 |
| F1.4 | 登录限流 | 同账号+IP 滑动窗口 5 次/5 分钟，超限 429 并提示剩余冷却时间 | 连点 5 次登录第 6 次被限，错误响应含 retry_after |
| F1.5 | RBAC | 依赖注入 `require_role("admin")`，接口级控制 | 普通用户访问管理接口 403（不是 401） |

技术约束：pydantic-settings 管理配置；JWT 黑名单必须 Redis（面试埋点：为什么放 Redis——TTL 自动过期、多实例共享）。

### F2 资源域：节点管理

| 编号 | 功能点 | 需求描述 | 验收标准 |
|---|---|---|---|
| F2.1 | 节点模型 | `nodes`: hostname、CPU 核数、GPU 型号/数量、内存、状态(online/offline/draining)、tags JSONB | 表结构符合 ERD |
| F2.2 | 节点 CRUD | admin 增删改查；下线节点（draining）不再接收新作业 | 下线后新作业不分配到该节点 |
| F2.3 | 资源视图 | 用户可见节点列表（只读），含实时空闲卡数 | 列表页与 API 数据一致 |

### F3 配额域

| 编号 | 功能点 | 需求描述 | 验收标准 |
|---|---|---|---|
| F3.1 | 配额模型 | `quotas`: 用户组 × 资源维度（GPU 卡时池、最大并行节点数、单作业上限） | 一个用户组一条配额记录 |
| F3.2 | 配额扣减（防超卖核心） | 提交作业时 `SELECT ... FOR UPDATE` 行锁校验并预扣；或 `UPDATE quotas SET used=used+? WHERE id=? AND limit-used>=? RETURNING`，rowcount=0 则拒绝 | **并发测试**：`asyncio.gather` 20 并发提交，断言总预扣 ≤ 限额，无一超扣 |
| F3.3 | 配额返还 | 作业终态（Failed/Canceled）按实际未消耗部分返还；Completed 不返 | 取消运行中的作业，已消耗卡时照扣，剩余返还 |
| F3.4 | 配额管理 | admin 调整组配额；调整不影响已运行作业 | 调低配额不杀死在跑作业，只影响新提交 |
| F3.5 | 配额查询 | 用户查看本组当前配额余量 | 页面/API 数值与扣减逻辑一致 |

技术约束：扣减与作业入队必须在同一事务；JSONB 存配额明细字段（埋点：JSONB vs 固定列的取舍——维度会扩展）。

### F4 作业域（核心）

| 编号 | 功能点 | 需求描述 | 验收标准 |
|---|---|---|---|
| F4.1 | 作业模型 | `jobs`: name、用户、组、节点数、卡数、 walltime 上限、params JSONB、状态机、排队序号 | 状态机：Queued → Running → Completed/Failed/Canceled；Queued → Canceled |
| F4.2 | 作业提交 | Pydantic 校验（`Field(le=...)` 上限）→ 配额预扣 → 生成排队作业 → 事务提交 | 超上限字段返回 422 及明确字段错误；配额不足 409 及原因 |
| F4.3 | 模拟调度器 | 后台 worker（APScheduler/asyncio）：扫描 Queued 作业，按节点空闲+配额+先进先出分配到节点，置 Running 并记录 start_time；到 walltime 或随机故障率置 Failed/Completed，写 usage_events | 提交后秒级进入 Running；结束自动计费 |
| F4.4 | 取消作业 | `UPDATE jobs SET status='Canceled' WHERE id=? AND status IN ('Queued','Running') RETURNING`，rowcount=0 返回 409 | 只有排队/运行中可取消；已终态取消返回 409 |
| F4.5 | 作业列表 | 分页、按状态/时间筛选、用户看自己、admin 看全部 | TanStack Query 缓存 + 筛选参数同步 URL |
| F4.6 | 作业详情 | 状态、资源占用、起止时间、消耗卡时、日志（mock） | 详情页字段齐全 |
| F4.7 | SSE 实时推送 | `GET /jobs/stream`：作业状态变更经 Redis Pub/Sub 扇出，SSE 推给相关用户 | 两个浏览器同时打开列表，一处操作另一处 ≤2s 刷新（前端收到即 invalidateQueries） |

技术约束：所有状态流转必须 `UPDATE ... WHERE status IN (...)` 乐观状态检查，禁止读-改-写；调度器单实例先行（面试埋点：多实例调度用 PG advisory lock 或行锁抢占）。

### F5 计量计费域

| 编号 | 功能点 | 需求描述 | 验收标准 |
|---|---|---|---|
| F5.1 | 流水模型 | `usage_events`: 作业 ID、用户、组、GPU 数、开始、结束、时长、卡时（=GPU 数×时长）、费率快照 | 流水只增不改；取消的流水标记冲正（reversal）而非删除 |
| F5.2 | 计费规则 | 卡时 = GPU 数 × 实际时长（分钟，向上取整）；费率随节点类型（费率快照防历史账单被改价影响） | 手算 3 个 case 与系统一致 |
| F5.3 | 个人用量查询 | 用户按时间段查自己的卡时明细 | 明细合计 = 汇总值 |
| F5.4 | 组用量查询 | 组管理员/admin 查组内各成员用量排行 | 排行与明细对账一致 |

### F6 报表域

| 编号 | 功能点 | 需求描述 | 验收标准 |
|---|---|---|---|
| F6.1 | 月结物化视图 | `mv_monthly_billing`：月 × 用户 × 组 × 卡时合计；APScheduler 每小时 `REFRESH CONCURRENTLY` | 流水插入后 ≤1h 报表可见；刷新不锁表（CONCURRENTLY） |
| F6.2 | 经营看板 | ECharts：卡时趋势（日/月）、队列占用率、节点利用率热力图、TOP 用户/组 | 页面加载 <2s（记录 EXPLAIN ANALYZE 前后对比） |
| F6.3 | 慢查询优化 | 至少一处加复合索引/改写查询，前后耗时记入 metrics.md | metrics.md 有前后对比数字 |

### F7 审计域

| 编号 | 功能点 | 需求描述 | 验收标准 |
|---|---|---|---|
| F7.1 | 审计触发器 | users/quotas/nodes/jobs 的 UPDATE/DELETE 自动写 `audit_log`（表、行 ID、旧值、新值、操作人、时间） | 任一字段修改后 audit_log 有对应记录 |
| F7.2 | 操作人注入 | 每个请求经中间件 `SET LOCAL app.user = '<user_id>'`，触发器 `current_setting('app.user')` 取操作人 | 日志操作人字段与实际登录人一致（含异步 worker 用系统账号） |
| F7.3 | 审计查询 | admin 按表/操作人/时间查询审计日志 | 筛选可用 |

### F8 平台管理（运维支撑）

| 编号 | 功能点 | 需求描述 | 验收标准 |
|---|---|---|---|
| F8.1 | 用户管理 | admin 创建用户、分配角色、重置密码 | 新用户可登录，角色生效 |
| F8.2 | 用户组管理 | 组的增删改、成员维护 | 配额绑定组后组不可硬删（软删或拒绝） |
| F8.3 | 健康检查 | `/healthz`（API+DB+Redis 探活），供 compose/caddy 探活 | 断开 Redis 时 /healthz 返回 503 |

---

## 4. 数据模型（ERD 摘要）

```
users ──┬──< user_groups >── groups ──< quotas
        └──< jobs >── nodes            （组 1:1 配额）
jobs ──< usage_events
audit_log （独立，触发器写入）
mv_monthly_billing （物化视图，源于 usage_events）
```

关键字段与约束：

| 表 | 关键字段 | 关键约束 |
|---|---|---|
| users | id, email(uniq), password_hash, role, is_active | email 唯一索引 |
| groups | id, name(uniq), description | name 唯一 |
| quotas | group_id(FK), gpu_limit, gpu_used, max_nodes_per_job, max_gpus_per_job, detail JSONB | group_id 唯一；gpu_used ≤ gpu_limit 应用层保证 |
| nodes | id, hostname(uniq), cpu_cores, gpu_total, gpu_used, mem_gb, status, tags JSONB | hostname 唯一；gpu_used ≤ gpu_total |
| jobs | id, user_id, group_id, name, num_nodes, num_gpus, walltime_min, params JSONB, status, node_id, queued_at, started_at, ended_at | status CHECK IN (...)；索引 (status, queued_at)、(user_id, created_at) |
| usage_events | id, job_id, user_id, group_id, gpus, started_at, ended_at, duration_min, gpu_hours, rate_snapshot JSONB, reversed bool | 只增不改；索引 (group_id, started_at)、(user_id, started_at) |
| audit_log | id, table_name, row_id, action, old_val JSONB, new_val JSONB, actor, created_at | 只增不改 |
| refresh_tokens | id, user_id, token_hash(uniq), expires_at, revoked | token 存哈希不存明文 |

---

## 5. API 清单（REST）

### 认证
| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| POST | /api/v1/auth/login | 公开+限流 | 登录，返回 token 对 |
| POST | /api/v1/auth/refresh | refresh token | 换新 access |
| POST | /api/v1/auth/logout | 登录 | 拉黑 access + 吊销 refresh |
| GET | /api/v1/auth/me | 登录 | 当前用户信息 |

### 节点
| GET | /api/v1/nodes | 登录 | 节点列表（用户只读） |
| POST/PUT/DELETE | /api/v1/nodes | admin | 节点管理 |

### 配额
| GET | /api/v1/quotas/me | 登录 | 本组配额余量 |
| GET/PUT | /api/v1/quotas/{group_id} | admin | 查询/调整组配额 |

### 作业
| POST | /api/v1/jobs | 登录 | 提交作业（校验+预扣+入队） |
| GET | /api/v1/jobs | 登录 | 列表（分页筛选；user 只能看自己的） |
| GET | /api/v1/jobs/{id} | 登录 | 详情（仅本人/admin） |
| POST | /api/v1/jobs/{id}/cancel | 登录 | 取消（状态机检查） |
| GET | /api/v1/jobs/stream | 登录 | SSE 状态推送 |

### 计量
| GET | /api/v1/usage/me | 登录 | 我的卡时明细 |
| GET | /api/v1/usage/groups/{id} | admin/组管 | 组用量排行 |

### 报表
| GET | /api/v1/reports/monthly | admin | 月结汇总 |
| GET | /api/v1/reports/dashboard | admin | 看板聚合数据 |

### 审计/管理
| GET | /api/v1/audit-logs | admin | 审计查询 |
| GET/POST/PUT | /api/v1/users, /api/v1/groups | admin | 用户/组管理 |
| GET | /healthz | 公开 | 探活 |

**统一响应约定**：`{code: 0, data: ...}` / 错误 `{code: <业务码>, message, detail}`；分页 `{items, total, page, size}`。422 校验错误带 field-level detail。

---

## 6. 前端页面清单（Next.js 14）

| 页面 | 路由 | 功能 | 关键交互 |
|---|---|---|---|
| 登录页 | /login | F1.1-1.4 | 限流提示、错误展示 |
| 作业列表 | /jobs | F4.5, F4.7 | Data Table、状态徽标、SSE 自动刷新、筛选同步 URL |
| 提交作业 | /jobs/new | F4.2 | 表单校验（前后端同规则）、配额余量实时显示 |
| 作业详情 | /jobs/[id] | F4.6 | 时间线、消耗卡时、日志 |
| 我的用量 | /usage | F5.3 | 明细表 + 汇总卡 |
| 节点列表 | /nodes | F2.3 | 只读表格 + GPU 占用条 |
| 管理-节点 | /admin/nodes | F2.2 | CRUD + 上下线 |
| 管理-配额 | /admin/quotas | F3.4 | 组配额编辑 |
| 管理-报表 | /admin/reports | F6.2 | ECharts 看板 |
| 管理-审计 | /admin/audit | F7.3 | 筛选表格 |
| 管理-用户/组 | /admin/users, /admin/groups | F8.1-8.2 | CRUD |

技术栈约定：App Router + TanStack Query + shadcn/ui + openapi-typescript 生成 client。

---

## 7. 非功能需求

| 类别 | 要求 |
|---|---|
| 并发正确性 | 20 并发提交不超扣（自动化测试常驻 CI） |
| 性能 | 列表/详情 API p95 < 200ms（种子数据 1 万作业）；看板页加载 < 2s |
| 安全 | bcrypt(12)；JWT 黑名单；参数化查询；管理接口全量鉴权；限流；密钥走环境变量 |
| 审计 | 敏感表 100% 覆盖 UPDATE/DELETE |
| 可观测 | 结构化日志；关键操作记 metrics.md（耗时、并发数、命中率） |
| 部署 | docker compose 一键起（api + postgres + redis + caddy + worker）；Makefile 封装 |

---

## 8. 里程碑映射（与原执行计划对齐）

| 里程碑 | 覆盖模块 | 验收 |
|---|---|---|
| M1（1-2 周） | F1 全部 + F8.3 + 骨架 | 两角色登录、越权 403、限流生效、登出失效 |
| M2（3-5 周） | F2, F3, F4（除 SSE）, F5 | 全链路跑通 + 20 并发测试绿 |
| M3（6-7 周） | F4.7 SSE + F2.3 前端 + 管理页 | 双浏览器秒级刷新 |
| M4（8 周） | F6, F7 + README + 上线 | demo 视频 + 线上地址 + metrics.md 数据齐 |

可选砍项（延期时先砍）：F4.6 日志、F6.2 热力图、分片上传。
不可砍项：F3.2 防超卖、F5 计费流水、F7.1 审计——这是业务立身之本。

---

## 9. 面试埋点速查

1. 配额扣减：悲观锁 vs `UPDATE WHERE limit-used>=? RETURNING`；超卖的两种解法
2. JWT 黑名单为什么 Redis：TTL=剩余有效期自动过期 + 多实例共享
3. 状态机：`UPDATE WHERE status IN (...)` 检查 rowcount，禁读-改-写
4. usage_events 为什么只增不改 + reversal 冲正：对账可信
5. 物化视图 REFRESH CONCURRENTLY：报表不锁业务表
6. 审计触发器 + `current_setting('app.user')`：操作人如何进触发器
7. SSE vs WebSocket；Redis Pub/Sub 多实例扇出
8. 业务叙事："可计量、可结算、可追溯"（对标润宇 Unify 平台运营服务）
