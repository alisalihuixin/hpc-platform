# 项目② 集群故障诊断 Agent · 需求文档（PRD v1.0）

> 版本：v1.0 ｜ 2026-11-17 ｜ 状态：可进入开发
> 定位：面向超算/智算中心的**可解释故障诊断**（对标 Datadog Bits AI / 阿里云 SRE Agent 的排障场景）
> 业务一句话：把「告警来了，人肉翻日志猜原因」变成「Agent 按证据链推理，给出可复核的根因报告」。

---

## 1. 业务背景与目标

### 1.1 业务逻辑

超算中心的一次故障处理，本质是一条**证据驱动的收敛过程**：

```
告警 → 采集现场证据（节点/GPU/日志/指标/手册） → 提出假设 → 逐条验证 → 根因报告 → 人工复核 → 回流评估
```

| 业务概念 | 技术实现 | 类比 |
|---|---|---|
| 告警 | `alerts` 表（20 个故障剧本注入） | 案件受理 |
| 诊断工具 | 6 个 Pydantic 约束的工具函数 | 现场勘查 |
| 诊断轨迹 | LangGraph `DiagnosticState` 每步入 state | 办案记录 |
| 根因报告 | 结论 + 证据引用（evidence id） | 结案报告 |
| 人工复核 | `diagnosis_reviews` 表 | 复核归档 |
| 命中率 | 20 case 评估集 + 人工标注修正 | 破案率 |

### 1.2 业务目标

1. 运维：输入告警即可拿到带证据链的根因报告，不必手工翻 4 类数据源
2. 主管：能看到 Agent 每一步查了什么、为什么排除某个假设（可解释、可追责）
3. 系统：20 条故障剧本命中率 > 70%，5 个陷阱 case 是能力分水岭
4. 工程：每一步工具调用有 trace（Langfuse），失败可回放

### 1.3 非目标（Out of Scope）

- 不接真实 Prometheus / Slurm / 交换机（用 mock 数据 + 固定种子生成，预留 adapter）
- **不自动执行修复**：`execute_fix` 只产出建议，不进入模型可选工具列表（安全边界）
- 不做多集群纳管、不做告警去噪（关联分析仅限同批告警）
- 不做模型微调，只用 DeepSeek API（chat + reasoner 对比一次）

---

## 2. 用户角色与权限（RBAC，复用项目① JWT）

| 角色 | 标识 | 核心诉求 |
|---|---|---|
| 普通用户 | `user` | 提交告警诊断、看自己的诊断记录 |
| 运维 / 系统管理员 | `admin` | 全部诊断记录、批量关联诊断、人工复核、评估报告 |

| 功能 | user | admin |
|---|---|---|
| 提交单条告警诊断 | ✓ | ✓ |
| 查看诊断过程与报告 | 仅自己 | ✓ |
| 批量关联诊断（M7） | ✗ | ✓ |
| 人工复核标注 | ✗ | ✓ |
| 评估报告 / trace 回放 | ✗ | ✓ |

---

## 3. 功能模块总览

| 编号 | 模块 | 里程碑 | 说明 |
|---|---|---|---|
| F1 | Mock 数据与故障剧本 | M5 | 50 节点、GPU 状态、日志、200 条历史工单、20 剧本（5 陷阱） |
| F2 | 工具层 | M5 | 6 个工具，Pydantic 约束入出参，超时与异常统一包装 |
| F3 | 诊断编排 | M6 | LangGraph StateGraph：plan → call_tool → observe → reflect → report |
| F4 | 可观测 | M6 | Langfuse self-hosted，每次诊断完整 trace |
| F5 | 评估 | M6 | 20 case 命中率；chat vs reasoner 对比 |
| F6 | 并行多案 + 关联分析 | M7 | Supervisor + `Send` 并行子图 + join 汇总 |
| F7 | 人工复核回流 | M7 | 报告页标注 → review 表 → 评估合并修正 |
| F8 | 前端 | M6/M7 | 诊断列表、时间线、报告页、复核、批量结果 |

### F1 数据与剧本

- 固定种子（`faker.seed(20261117)`）保证可复现
- 50 节点：hostname、型号、8×A100-80G、健康状态、所在交换机/机架
- 每剧本包含：告警文案、真值根因、应查证据、陷阱说明
- 20 剧本明细：

| # | 剧本 | 真值根因 | 类型 |
|---|---|---|---|
| 1 | 作业 OOM | 单进程显存超限 | 常规 |
| 2 | 磁盘写失败 | `/data` 满 | 常规 |
| 3 | 节点失联 | 网络分区 | 常规 |
| 4 | GPU 利用率 100% 但无作业 | 残留进程 | 常规 |
| 5 | 提交被拒 | 组配额超限 | 常规 |
| 6 | 作业随机失败 | GPU ECC 错误 | 常规 |
| 7 | 长跑作业越来越慢 | 显存泄漏 | 陷阱 |
| 8 | 多机训练卡住 | NCCL 超时（MTU） | 常规 |
| 9 | 调度不分配 | 节点 schedulable=false | 常规 |
| 10 | 训练吞吐骤降 | 磁盘 IO 打满 | 常规 |
| 11 | 镜像拉取失败 | DNS 解析异常 | 常规 |
| 12 | 读训练集卡死 | NFS 服务端挂起 | 陷阱 |
| 13 | 作业被 kill | cgroup 内存超限 | 常规 |
| 14 | 新节点跑不了 | 驱动版本不匹配 | 常规 |
| 15 | 多卡通信慢 | PCIe 降速 | 常规 |
| 16 | 日志时间戳错乱 | NTP 漂移 | 常规 |
| 17 | 偶发丢包 | 交换机端口错包 | 陷阱 |
| 18 | GPU 显存被占 | 他人进程占用（非故障） | 陷阱 |
| 19 | CPU 使用率高 | cgroup CPU throttling | 常规 |
| 20 | 作业起不来 | 许可证不足 | 陷阱 |

> 陷阱 case 的共同点：**表象与真值不一致**，只看第一条告警必然误判，必须交叉验证两类以上证据。

### F2 工具层（6 个）

| 工具 | 入参 | 出参要点 |
|---|---|---|
| `get_node_status` | hostname / node_id | 状态、schedulable、最近心跳、交换机 |
| `get_job_logs` | job_id、tail_lines(≤2000) | 结构化日志行 + 命中关键字 |
| `get_node_gpus` | hostname | 每卡显存/利用率/进程/ECC |
| `get_metrics` | hostname、metric、window_min(≤180) | 序列点 + 同比基线 |
| `search_runbooks` | query(k≤5) | runbook 片段 + 来源 id（M8 升级为 RAG） |
| `execute_fix` | hostname、action | **仅生成建议，不注册为模型工具** |

约束：单工具超时 10s、异常转为结构化错误（不抛给模型栈）、每次调用写 `diagnosis_steps`。

### F3 诊断编排（LangGraph）

状态机：

```
plan ──► call_tool ──► observe ──► reflect ─┬─► call_tool（未收敛且未超限）
                                            ├─► report（证据充分）
                                            └─► fallback（预算/步数耗尽，降级规则结论）
```

- `DiagnosticState`：`alert / evidence[] / tool_calls[] / iterations / budget / hypothesis / report / trace_id`
- 硬约束：`max_iterations=6`、`recursion_limit`、已调工具+参数去重（防烧钱）、token 预算递减
- 报告要求：每条结论必须带 `evidence_id`，无证据的结论不输出（防幻觉）
- 幂等：同一 `alert_id` 10 分钟内重复触发复用上次结果

### F4 可观测

- Langfuse self-hosted（compose 增加 service），每次诊断一个 trace，含每步耗时/token
- 报告页可跳到对应 trace

### F5 评估

- `scripts/eval_diagnosis.py`：20 case 全跑，输出命中率、平均步数、平均 token、p95 耗时
- 一次 chat vs reasoner 对比，数据入 `docs/metrics.md`
- 命中判定：报告根因与真值根因标签一致（人工复核可覆盖）

### F6 并行多案（M7）

- 输入一批关联告警 → Supervisor 判断是否同源 → `Send` 展开 N 个子图并行 → join 汇总
- 典型场景：同一交换机下 3 个节点同时失联 → 单案只会报「节点故障」，并行汇总才能得出「网络分区」
- 护栏：批量上限 10 条，总 token 预算共享

### F7 人工复核回流（M7）

- 报告页「根因正确 / 错误 + 正确根因」→ `diagnosis_reviews`
- 评估脚本合并人工标注重算命中率（区分「模型自评」与「人工确认」两个数字）

### F8 前端（复用项目① 布局/鉴权/SSE 客户端）

| 页面 | 内容 |
|---|---|
| `/diagnosis` | 告警列表、提交诊断、状态徽标、命中率概览 |
| `/diagnosis/[id]` | 时间线（工具卡片：名称/入参/返回/耗时）、证据链、根因报告、复核表单 |
| `/diagnosis/batch` | 批量告警输入、并行结果对比、关联结论 |
| `/eval` | 评估报告表（含 chat vs reasoner、复核修正后） |

---

## 4. 数据模型（ERD 摘要）

| 表 | 关键字段 | 说明 |
|---|---|---|
| `incident_nodes` | id, hostname, switch, rack, schedulable, heartbeat_at | mock 节点（不复用项目① nodes，避免污染计费域） |
| `alerts` | id, node_id, script_key, severity, summary, fired_at | 20 剧本注入 |
| `diagnosis_runs` | id, alert_id, batch_id, status, root_cause, confidence, iterations, tokens, trace_id, duration_ms | 一次诊断 |
| `diagnosis_steps` | id, run_id, seq, node(plan/tool/reflect), tool_name, args JSONB, result JSONB, latency_ms | 证据链 |
| `diagnosis_reviews` | id, run_id, verdict(correct/wrong), corrected_cause, reviewer_id, created_at | 人工复核 |
| `runbooks` | id, title, body, tags | 5 个工具之一的数据源 |
| `eval_cases` | id, script_key, expected_cause, trap, notes | 评估集（与 alerts 解耦） |

---

## 5. API 清单（REST + SSE）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/alerts` | 注入告警（mock 剧本 key 或自定义） |
| GET | `/api/v1/alerts` | 告警列表（状态/严重级过滤） |
| POST | `/api/v1/diagnosis` | 触发诊断，返回 run_id |
| GET | `/api/v1/diagnosis` | 诊断记录列表 |
| GET | `/api/v1/diagnosis/{id}` | 报告 + 步骤 + 证据链 |
| GET | `/api/v1/diagnosis/{id}/stream` | SSE：逐步推送 plan/tool/reflect/report |
| POST | `/api/v1/diagnosis/{id}/review` | 人工复核（admin） |
| POST | `/api/v1/diagnosis/batch` | 批量并行诊断（admin，M7） |
| GET | `/api/v1/eval/report` | 评估结果（admin） |
| GET | `/api/v1/runbooks/search` | 手册检索（工具同源） |

---

## 6. 非功能需求

| 项 | 目标 |
|---|---|
| 单案耗时 | p95 ≤ 60s（mock 工具 < 50ms，主要耗时在 LLM） |
| 单案 token | ≤ 40k，超预算降级规则结论 |
| 工具超时 | 10s，超时记为证据缺失而非失败 |
| 证据可溯源 | 报告结论 100% 带 evidence_id |
| 幂等 | 同 alert 10 分钟内复用结果 |
| 安全边界 | 修复动作永不自动执行 |
| 可复现 | 数据固定种子；诊断轨迹入库，可离线回放 |
| 测试 | 工具层与图节点单测覆盖率 ≥ 85%；20 case 端到端脚本 |

---

## 7. 里程碑映射

| 里程碑 | 周次 | 天次 | 内容 | 验收 |
|---|---|---|---|---|
| M5 数据与工具层 | W9–W10 | D41–D50 | F1、F2 | pytest 对 20 剧本跑工具层全通 |
| M6 单案诊断 Agent | W11–W12 | D51–D60 | F3、F4、F5、F8(单案) | 告警→SSE 流式诊断→根因报告；命中率 > 70% |
| M7 并行多案 + 复核回流 | W13 | D61–D65 | F6、F7、F8(批量/评估) | 5 条关联告警得出「网络分区」；评估含人工复核修正 |

## 8. 验收标准（演示不动 = 没做）

1. 选一个陷阱 case（如 #7 显存泄漏），完整演示：时间线能看到「先查 OOM 日志 → 排除 → 查 GPU 显存趋势 → 确认泄漏」
2. 批量灌入同交换机 5 条告警，给出一条关联结论而非 5 条孤立报告
3. 评估报告三列数字：模型自评命中率、人工复核后命中率、平均 token/耗时
4. 断网/无 API key 时降级为规则诊断，页面不崩

## 9. 面试埋点速查

- 为什么是 LangGraph 不是 Chain：需要**循环 + 分支 + 步数上限**，Chain 无法表达「证据不足回头再查」
- 防死循环防烧钱：`max_iterations` + `recursion_limit` + 工具去重 + token 预算递减 + 降级规则
- 证据链防幻觉：结论必须引用 evidence_id，没有证据就不下结论
- 多 Agent 的存在价值：单案诊断无法做跨节点关联，「同一交换机下 5 个节点失联」只有并行汇总能得出网络分区
- 评估怎么做才不骗自己：固定种子 + 陷阱 case + 人工复核覆盖模型自评
- 安全边界：`execute_fix` 不进工具列表，模型只能建议不能执行
