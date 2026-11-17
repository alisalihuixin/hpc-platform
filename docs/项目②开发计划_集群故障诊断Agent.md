# 项目② 集群故障诊断 Agent · 开发计划（逐日任务分解）

> 版本：v1.0 ｜ 2026-11-17
> 依据：`docs/项目②需求文档_集群故障诊断Agent.md`（PRD v1.0）+ `三项目执行计划_1012.md`
> 排期口径：单人力，1 天 = 1 个工作日（5 天/周），接项目①之后，共 **D41–D65**（W9–W13）
> 里程碑：M5(W9–W10) / M6(W11–W12) / M7(W13)

---

## 0. 总览

| 里程碑 | 周次 | 天次 | 覆盖模块 | 验收（DoD 汇总） |
|---|---|---|---|---|
| M5 数据与工具层 | W9–W10 | D41–D50 | F1、F2 | 20 剧本经工具层跑通；工具单测覆盖率 ≥ 85% |
| M6 单案诊断 Agent | W11–W12 | D51–D60 | F3、F4、F5、F8(单案) | SSE 流式诊断 + 根因报告；命中率 > 70% |
| M7 并行多案 + 复核回流 | W13 | D61–D65 | F6、F7、F8(批量/评估) | 5 条关联告警得出网络分区；评估含人工复核数字 |

**与项目①的复用（开工前确认）**

| 复用项 | 来源 | 用途 |
|---|---|---|
| monorepo 骨架 / Makefile / CI | 项目① | 直接加目录，不新建仓库 |
| JWT + RBAC 依赖 | `apps/api/app/deps/auth.py` | 诊断接口鉴权，加 `diagnosis` 权限点 |
| SSE 客户端与 hook | `apps/web/src/lib/api/job-events.ts`、`useJobEvents` | 诊断过程推送 |
| 前端布局 / 表格 / 徽标 / 提示组件 | `apps/web/src/components` | 诊断列表与报告页 |
| `core/config` / `db` / `workers` 模式 | 项目① | LLM key、迁移、后台评估任务 |
| 审计触发器 | `db/audit_triggers.py` | 复核标注写审计 |

---

## 1. M5：数据与工具层（D41–D50）

| Day | 任务 | 产出物 | 完成标志 |
|---|---|---|---|
| D41 | 骨架：`app/diagnosis/{data,tools,graph,eval}`；依赖 `langgraph` / `langfuse` / `faker`；迁移 `0010_diagnosis` | 6 张表迁移 upgrade/downgrade 双向可用 | `alembic upgrade head` 成功 |
| D42 | mock 数据：50 节点（交换机/机架/GPU/心跳）、GPU 每卡状态、日志样本、200 条历史工单，固定种子 | `app/diagnosis/data/mock.py` + `make seed-diagnosis` | 两次执行数据完全一致（快照比对） |
| D43 | 20 个故障剧本（含 5 陷阱）与评估集字段（真值/应查证据/陷阱说明） | `app/diagnosis/data/scripts.py` + 单测 | 剧本→告警→真值三者一致，20/20 |
| D44 | 工具 1–3：`get_node_status` / `get_job_logs` / `get_node_gpus`，统一超时 10s 与结构化错误 | `app/diagnosis/tools/*.py` | 单测覆盖正常/不存在/超时三类返回 |
| D45 | 工具 4–6：`get_metrics`（window ≤180）/ `search_runbooks` / `execute_fix`（仅建议、不入工具表） | 工具注册表 `TOOLS` | `execute_fix` 不在模型可选列表中（断言） |
| D46 | 工具层回归：20 剧本按「应查证据」依次调用，断言每条证据可取到 | `tests/diagnosis/test_tools_scripts.py` | 20/20 通过 |
| D47 | DeepSeek 调用层：chat/reasoner 切换、JSON Mode、重试与超时、token 计量 | `app/core/llm.py` | 假客户端单测：正常/重试/熔断 |
| D48 | 成本与预算：每案 token 累计、40k 上限、超限降级钩子 | `app/diagnosis/budget.py` | 单测：累计与降级触发 |
| D49 | 告警与诊断记录 API：`POST /alerts`、`POST /diagnosis`、`GET /diagnosis/{id}`（落库） | `routers/diagnosis.py` | HTTP 用例：提交→记录可查 |
| D50 | M5 验收：`pytest` 全绿；工具耗时与数据规模写入 `metrics.md` | 验收记录 | 工具 p95 < 50ms（mock） |

## 2. M6：单案诊断 Agent（D51–D60）

| Day | 任务 | 产出物 | 完成标志 |
|---|---|---|---|
| D51 | LangGraph 骨架：`DiagnosticState` + `plan` / `call_tool` / `observe` 节点 + 条件边 | `app/diagnosis/graph/graph.py` | 图可编译，dry-run 走通一轮 |
| D52 | `reflect` + `report` 节点；报告结论强制引用 `evidence_id` | 报告 schema | 无证据结论被校验拒绝（单测） |
| D53 | 循环护栏：`max_iterations=6`、`recursion_limit`、工具+参数去重、预算递减、降级规则结论 | 护栏单测 | 构造死循环场景，6 步内终止 |
| D54 | 步骤落库 + SSE 推送（`/diagnosis/{id}/stream`）；复用项目① SSE 客户端 | `stream` 端点 + `useDiagnosisEvents` | curl 可逐步收到 plan/tool/reflect/report |
| D55 | 前端：`/diagnosis` 列表 + `/diagnosis/[id]` 时间线（工具卡片：名称/入参/返回/耗时）+ 报告区 | 页面 2 个 | 手动触发一次能看到完整时间线 |
| D56 | Langfuse self-hosted compose 接入，`trace_id` 落库并可从报告页跳转 | compose + 配置 | 一次诊断在 Langfuse 可见全链路 |
| D57 | 评估脚本首跑：20 case → 命中率/平均步数/token/p95 | `scripts/eval_diagnosis.py` + 基线数据 | 输出 JSON 报告，基线入 `metrics.md` |
| D58 | chat vs reasoner 对比；逐条分析陷阱 case 失败原因 | 对比表 | 两组数字并列记录 |
| D59 | 针对 badcase 调 prompt / 工具选择策略，二跑评估 | 改动 + 二跑数据 | 命中率较基线提升（记录前后） |
| D60 | M6 验收：命中率 > 70%；单案 p95 ≤ 60s；`metrics.md` 更新 | 验收记录 | 演示：陷阱 case 完整推理链 |

## 3. M7：并行多案 + 复核回流（D61–D65）

| Day | 任务 | 产出物 | 完成标志 |
|---|---|---|---|
| D61 | Supervisor 节点 + `Send` API 并行展开子图（批量上限 10） | `graph/batch.py` | 5 个告警并行执行，总耗时 < 单案 × 5 |
| D62 | join 汇总 + 关联分析 prompt（同交换机/同机架聚合） | 汇总节点 | 灌入同交换机 5 条告警，输出「网络分区」 |
| D63 | 复核：`diagnosis_reviews` 表 + `POST /diagnosis/{id}/review` + 报告页表单（根因正确/错误 + 正确根因） | 表 + API + 表单 | 复核写入后报告页显示复核状态 |
| D64 | 评估合并人工标注重算命中率；`/eval` 页展示三列（自评/复核后/reasoner） | 评估脚本升级 + 页面 | 两个命中率数字同时可见 |
| D65 | M7 收口：README（架构图/ADR/量化数据）、批量演示、D41–D65 回填 | 文档 | 一次 5 条关联告警演示通过 |

---

## 4. 每日 DoD

1. 代码可运行，新增路径有对应测试（工具/图节点/接口至少一层）
2. `ruff check` / `ruff format --check` / `pytest` 全绿
3. 当天产生的新数字（耗时/命中率/token）写进 `docs/metrics.md`
4. 当天提交，commit message 说明「为什么」

## 5. 风险与缓冲

| 风险 | 影响 | 应对 |
|---|---|---|
| LLM 输出不稳定导致评估波动 | 命中率不可比 | 固定 prompt 版本号 + 温度 0 + 种子；对比只用同一评估集 |
| 陷阱 case 命中率上不去 | M6 验收卡住 | 优先保证「排除路径可解释」；必要时把陷阱 case 拆成独立指标 |
| LangGraph 版本 API 变动 | 骨架返工 | 锁定版本；图逻辑集中在 `graph/` 单层，节点函数可独立测 |
| Langfuse self-hosted 资源占用 | 本机跑不动 | 失败则降级为本地 JSON trace，接口不变 |
| 无 API key / 断网 | 演示失败 | 规则降级路径（D53）必须能独立出报告 |
| 时间超支 | 拖累项目③ | 砍 `reasoner` 对比（D58）与批量上限（保持 5 条） |

## 6. 面试埋点（每个里程碑口头过一遍）

- **M5**：mock 为什么要固定种子（可复现的评估前提）；工具为什么用 Pydantic 约束（模型幻觉出非法参数时在边界拦截）；`execute_fix` 为什么不给模型（安全边界）
- **M6**：LangGraph vs Chain（循环/分支/步数上限）；防死循环与烧钱的四道闸；证据链如何防幻觉；评估为什么要陷阱 case
- **M7**：`Send` 并行与 join 汇总；为什么关联诊断是单 Agent 做不到的（多 Agent 存在价值的实证）；人工复核如何修正自评偏差
