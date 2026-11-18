"""The 20 fault scripts and their evaluation ground truth (D43).

A script is three things that must stay aligned: the alert an operator sees
(``title``), the cause that is actually true (``expected_cause``) and the
evidence an agent has to consult to get there (``expected_evidence``).  Five of
them are traps (positions 7/12/17/18/20): the first symptom points at a
plausible-but-wrong cause, and only a second tool call can tell them apart.
``misdirection`` records the wrong answer so D57/D64 can report trap accuracy
separately instead of burying it in the overall hit rate.

Hostnames refer to the deterministic fleet in :mod:`app.diagnosis.data.mock`:
10 racks x 5 nodes, one switch per rack.  ``_sw_hosts`` therefore also encodes
the M7 correlation demo (five alerts on one switch).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import async_session_factory
from app.diagnosis.data.mock import BASE_TIME
from app.models.diagnosis import Alert, AlertSeverity, AlertStatus, EvalCase, IncidentNode

#: Read-only tools an agent may consult.  ``execute_fix`` is deliberately absent:
#: the model may propose a fix but never run it (safety boundary, D45).
EVIDENCE_TOOLS = frozenset(
    {"get_node_status", "get_job_logs", "get_node_gpus", "get_metrics", "search_runbooks"}
)


@dataclass(frozen=True, slots=True)
class FaultScript:
    key: str
    title: str
    severity: AlertSeverity
    expected_cause: str
    expected_evidence: tuple[dict[str, Any], ...]
    hostnames: tuple[str, ...] = ()
    trap: bool = False
    #: The answer the first alert tempts you into; only set for traps.
    misdirection: str | None = None
    #: Why the misdirection fails - stored as ``eval_cases.notes``.
    trap_note: str | None = None


def _evidence(tool: str, **args: Any) -> dict[str, Any]:
    return {"tool": tool, "args": args}


def _sw_hosts(first_index: int) -> tuple[str, ...]:
    """The five nodes of one rack/switch, e.g. 1 -> gpu-001..gpu-005."""
    return tuple(f"gpu-{index:03d}" for index in range(first_index, first_index + 5))


SCRIPTS: tuple[FaultScript, ...] = (
    FaultScript(
        key="gpu_ecc_uncorrectable",
        title="gpu-003 GPU2 ECC 不可纠正错误 12 次，训练作业频繁中断",
        severity=AlertSeverity.CRITICAL,
        hostnames=("gpu-003",),
        expected_cause="GPU2 显存 ECC 不可纠正错误，需下线换卡",
        expected_evidence=(
            _evidence("get_node_gpus", hostname="gpu-003"),
            _evidence("get_job_logs", hostname="gpu-003", limit=50),
        ),
    ),
    FaultScript(
        key="gpu_xid_79_off_bus",
        title="gpu-007 GPU6 报 Xid 79，卡已从 PCIe 总线掉线",
        severity=AlertSeverity.CRITICAL,
        hostnames=("gpu-007",),
        expected_cause="GPU6 PCIe 链路错误（Xid 79），卡已掉线",
        expected_evidence=(
            _evidence("get_node_gpus", hostname="gpu-007"),
            _evidence("get_job_logs", hostname="gpu-007", limit=50),
        ),
    ),
    FaultScript(
        key="gpu_xid_79_nvlink",
        title="gpu-013 GPU4 报 Xid 79，NVLink 带宽异常",
        severity=AlertSeverity.CRITICAL,
        hostnames=("gpu-013",),
        expected_cause="GPU4 Xid 79，NVLink/PCIe 链路错误",
        expected_evidence=(
            _evidence("get_node_gpus", hostname="gpu-013"),
            _evidence("get_node_status", hostname="gpu-013"),
        ),
    ),
    FaultScript(
        key="node_offline_heartbeat",
        title="gpu-029 心跳丢失 8 小时，scheduler 标记 NotReady",
        severity=AlertSeverity.CRITICAL,
        hostnames=("gpu-029",),
        expected_cause="节点 BMC 自重启，心跳中断，等待上电完成",
        expected_evidence=(
            _evidence("get_node_status", hostname="gpu-029"),
            _evidence("get_job_logs", hostname="gpu-029", limit=50),
        ),
    ),
    FaultScript(
        key="node_cordoned",
        title="gpu-009 长期不派单，疑似节点故障",
        severity=AlertSeverity.WARNING,
        hostnames=("gpu-009",),
        expected_cause="节点被运维 cordon（schedulable=false），并非硬件故障",
        expected_evidence=(
            _evidence("get_node_status", hostname="gpu-009"),
            _evidence("search_runbooks", tags="cordon,schedulable"),
        ),
    ),
    FaultScript(
        key="train_oom",
        title="gpu-011 训练作业显存 OOM 退出",
        severity=AlertSeverity.WARNING,
        hostnames=("gpu-011",),
        expected_cause="作业 batch_size 过大导致显存超限",
        expected_evidence=(
            _evidence("get_job_logs", hostname="gpu-011", limit=50),
            _evidence("get_node_gpus", hostname="gpu-011"),
        ),
    ),
    FaultScript(
        key="ecc_blamed_for_slowdown",
        title="gpu-003 ECC 告警后同交换机训练整体变慢",
        severity=AlertSeverity.CRITICAL,
        hostnames=_sw_hosts(1),
        expected_cause="交换机 sw-01 上联端口抖动：同交换机 5 节点 NCCL 同时超时，ECC 为历史遗留",
        expected_evidence=(
            *(_evidence("get_node_status", hostname=hostname) for hostname in _sw_hosts(1)),
            _evidence("get_job_logs", hostname="gpu-003", limit=50),
        ),
        trap=True,
        misdirection="GPU2 的 ECC 错误导致算力下降",
        trap_note=(
            "把降速归因到单卡 ECC 是典型误判：必须查同交换机另外 4 台节点的 NCCL 日志，"
            "5 台同时超时才指向网络，单台超时才指向 GPU"
        ),
    ),
    FaultScript(
        key="nccl_timeout_ib",
        title="gpu-016 NCCL all-reduce 超时，多机任务卡住",
        severity=AlertSeverity.CRITICAL,
        hostnames=("gpu-016",),
        expected_cause="IB 端口链路抖动导致 NCCL 集合通信超时",
        expected_evidence=(
            _evidence("get_job_logs", hostname="gpu-016", limit=50),
            _evidence("get_node_status", hostname="gpu-016"),
        ),
    ),
    FaultScript(
        key="switch_partition",
        title="sw-02 上 5 台节点同时失联，疑似网络分区",
        severity=AlertSeverity.CRITICAL,
        hostnames=_sw_hosts(6),
        expected_cause="交换机 sw-02 上联端口抖动，构成网络分区",
        expected_evidence=(
            *(_evidence("get_node_status", hostname=hostname) for hostname in _sw_hosts(6)),
            _evidence("get_metrics", window=180),
        ),
    ),
    FaultScript(
        key="rack_pdu_failure",
        title="机架 rack-f 多节点掉电，冷却与供电告警",
        severity=AlertSeverity.CRITICAL,
        hostnames=_sw_hosts(26),
        expected_cause="机架 rack-f 单路 PDU 掉电，双路冗余未切换",
        expected_evidence=(
            *(_evidence("get_node_status", hostname=hostname) for hostname in _sw_hosts(26)),
            _evidence("get_metrics", window=180),
        ),
    ),
    FaultScript(
        key="quota_exhausted",
        title="用户组 GPU 配额用尽，作业持续排队",
        severity=AlertSeverity.WARNING,
        expected_cause="用户组 gpu_limit 已打满，需扩容或等待释放",
        expected_evidence=(
            _evidence("get_metrics", window=180),
            _evidence("search_runbooks", tags="quota,扩容"),
        ),
    ),
    FaultScript(
        key="idle_node_not_crash",
        title="gpu-009 长时间空闲，疑似宕机",
        severity=AlertSeverity.WARNING,
        hostnames=("gpu-009",),
        expected_cause="节点被 cordon，心跳与 GPU 状态正常，并非宕机",
        expected_evidence=(
            _evidence("get_node_status", hostname="gpu-009"),
            _evidence("get_node_gpus", hostname="gpu-009"),
        ),
        trap=True,
        misdirection="节点宕机或离线，需要重启 BMC",
        trap_note="空闲不等于宕机：先看 heartbeat_at 与 schedulable，再决定是否重启",
    ),
    FaultScript(
        key="disk_full_docker",
        title="gpu-011 /var/lib/docker 写满，容器启动失败",
        severity=AlertSeverity.WARNING,
        hostnames=("gpu-011",),
        expected_cause="/var/lib/docker 写满，镜像层无法写入",
        expected_evidence=(
            _evidence("get_job_logs", hostname="gpu-011", limit=50),
            _evidence("get_node_status", hostname="gpu-011"),
        ),
    ),
    FaultScript(
        key="licence_expiry",
        title="调度器 License 将于 7 天后到期",
        severity=AlertSeverity.INFO,
        expected_cause="License 7 天后到期，需要续期",
        expected_evidence=(
            _evidence("get_metrics", window=180),
            _evidence("search_runbooks", tags="license,续期"),
        ),
    ),
    FaultScript(
        key="scheduler_backlog_cordon",
        title="队列积压 40 个作业，但配额仍有余额",
        severity=AlertSeverity.WARNING,
        hostnames=("gpu-009",),
        expected_cause="唯一空闲节点被 cordon，queued 作业无法出队",
        expected_evidence=(
            _evidence("get_node_status", hostname="gpu-009"),
            _evidence("get_metrics", window=60),
        ),
    ),
    FaultScript(
        key="ib_link_down",
        title="gpu-021 ib0 无载波，跨机通信全断",
        severity=AlertSeverity.CRITICAL,
        hostnames=("gpu-021",),
        expected_cause="gpu-021 光模块故障，ib0 链路 down",
        expected_evidence=(
            _evidence("get_node_status", hostname="gpu-021"),
            _evidence("get_job_logs", hostname="gpu-021", limit=50),
        ),
    ),
    FaultScript(
        key="disk_full_blames_training",
        title="gpu-011 训练作业把本地盘写满",
        severity=AlertSeverity.WARNING,
        hostnames=("gpu-011",),
        expected_cause="镜像层堆积导致 /var/lib/docker 写满，训练作业实际只写入 2GB",
        expected_evidence=(
            _evidence("get_job_logs", hostname="gpu-011", limit=50),
            _evidence("get_metrics", window=180),
        ),
        trap=True,
        misdirection="训练作业的 checkpoint/日志写满磁盘",
        trap_note="先量作业实际写入量再归因：日志里的写盘错误是结果，不是原因",
    ),
    FaultScript(
        key="nccl_blames_gpu",
        title="gpu-016 GPU 异常导致 NCCL 超时",
        severity=AlertSeverity.CRITICAL,
        hostnames=_sw_hosts(16),
        expected_cause="交换机 sw-04 上联抖动，gpu-016..020 同时 NCCL 超时",
        expected_evidence=(
            *(_evidence("get_node_status", hostname=hostname) for hostname in _sw_hosts(16)),
            _evidence("get_node_gpus", hostname="gpu-016"),
        ),
        trap=True,
        misdirection="gpu-016 单卡故障，需要换卡",
        trap_note="同交换机 5 台节点同时超时才说明是网络；只看单节点会误换卡",
    ),
    FaultScript(
        key="job_evicted_drain",
        title="gpu-030 运行中作业被驱逐",
        severity=AlertSeverity.WARNING,
        hostnames=("gpu-030",),
        expected_cause="节点进入 drain 维护窗口，运行中作业被驱逐",
        expected_evidence=(
            _evidence("get_node_status", hostname="gpu-030"),
            _evidence("get_job_logs", hostname="gpu-030", limit=50),
        ),
    ),
    FaultScript(
        key="xid_blames_overheat",
        title="gpu-042 温度告警后 GPU1 报 Xid 79",
        severity=AlertSeverity.CRITICAL,
        hostnames=("gpu-042",),
        expected_cause="PCIe 链路错误导致 Xid 79，温度 41℃ 处于正常区间",
        expected_evidence=(
            _evidence("get_node_gpus", hostname="gpu-042"),
            _evidence("get_node_status", hostname="gpu-042"),
        ),
        trap=True,
        misdirection="散热不足导致 GPU 过热掉卡，需要降低环境温度",
        trap_note="温度与 Xid 同屏出现时容易误判为散热；先看 Xid 类型与温度绝对值",
    ),
)


@dataclass(frozen=True, slots=True)
class ScriptSeedReport:
    inserted: int
    scripts: int
    alerts: int
    eval_cases: int


async def seed_fault_scripts(db: AsyncSession) -> ScriptSeedReport:
    """Insert one alert + one eval case per script; existing scripts are skipped.

    Per-script idempotency (instead of all-or-nothing) lets D59 add a script
    without wiping the alerts of the other nineteen.  The caller owns the commit.
    """
    known = set((await db.scalars(select(EvalCase.script_key))).all())
    node_ids = dict((await db.execute(select(IncidentNode.hostname, IncidentNode.id))).all())

    inserted = 0
    for index, script in enumerate(SCRIPTS):
        if script.key in known:
            continue
        fired_at = BASE_TIME + timedelta(minutes=5 * index)
        db.add(
            Alert(
                node_id=node_ids[script.hostnames[0]] if script.hostnames else None,
                script_key=script.key,
                severity=script.severity.value,
                summary=script.title,
                status=AlertStatus.OPEN.value,
                fired_at=fired_at,
                created_at=fired_at,
            )
        )
        db.add(
            EvalCase(
                script_key=script.key,
                expected_cause=script.expected_cause,
                trap=script.trap,
                expected_evidence=[dict(item) for item in script.expected_evidence],
                notes=script.trap_note,
            )
        )
        inserted += 1
    await db.flush()

    return ScriptSeedReport(
        inserted=inserted,
        scripts=len(SCRIPTS),
        alerts=int(await db.scalar(select(func.count()).select_from(Alert)) or 0),
        eval_cases=int(await db.scalar(select(func.count()).select_from(EvalCase)) or 0),
    )


async def main() -> None:  # pragma: no cover - CLI glue
    async with async_session_factory() as db:
        report = await seed_fault_scripts(db)
        await db.commit()
    state = "seeded" if report.inserted else "already seeded, skipped"
    print(
        f"{state}: scripts={report.scripts} alerts={report.alerts} eval_cases={report.eval_cases}"
    )


if __name__ == "__main__":  # pragma: no cover - CLI glue
    asyncio.run(main())
