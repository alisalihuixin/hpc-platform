"""Deterministic mock fleet + 200 historical tickets (D42).

Every value derives from ``SEED`` (faker's global seed is reset first), so two
runs of :func:`build_fleet` / :func:`seed_diagnosis_data` produce byte-identical
data.  That is the precondition for the evaluation numbers in ``docs/metrics.md``:
if the fleet drifted between runs, a hit-rate change could not be attributed to
the agent.

Layout: 10 racks x 5 nodes.  All five nodes of a rack share one switch, which is
what the M7 correlation demo ("5 alerts on sw-02 -> network partition") needs.
Two ground truths are pinned here for later scripts: ``gpu-003`` GPU 2 carries 12
uncorrectable ECC errors, and ``gpu-009`` is ``schedulable=False`` (the reason the
scheduler refuses it in script #9).
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, is_dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from faker import Faker
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import Base
from app.db.session import async_session_factory
from app.models.diagnosis import (
    Alert,
    AlertSeverity,
    AlertStatus,
    DiagnosisReview,
    DiagnosisRun,
    DiagnosisStep,
    IncidentNode,
    NodeHealth,
    ReviewVerdict,
    RunStatus,
    StepPhase,
)

SEED = 20261117
NODE_COUNT = 50
TICKET_COUNT = 200
NODES_PER_RACK = 5

#: Frozen "now" of the dataset; history is laid out backwards from here.
BASE_TIME = datetime(2026, 11, 17, 9, 0, tzinfo=UTC)

_NODE_PROFILES: tuple[tuple[str, str, int, int], ...] = (
    ("DGX-A100", "A100-80G", 8, 81920),
    ("DGX-H100", "H100-80G", 8, 81920),
    ("NF5488A5", "A100-40G", 8, 40960),
    ("R4900-G6", "A10-24G", 4, 24576),
)

#: node index -> health.  Indices are 0-based; index 8 is ``gpu-009``.
_STATUS_OVERRIDES: dict[int, NodeHealth] = {
    6: NodeHealth.DEGRADED,
    12: NodeHealth.DEGRADED,
    28: NodeHealth.OFFLINE,
    41: NodeHealth.DEGRADED,
}
_HEARTBEAT_LAG_MINUTES: dict[NodeHealth, int] = {
    NodeHealth.ONLINE: 1,
    NodeHealth.DEGRADED: 25,
    NodeHealth.OFFLINE: 480,
}

#: ``gpu-003`` GPU 2 - the ECC evidence behind the first evaluation script.
_ECC_NODE_INDEX = 2
_ECC_CARD_INDEX = 2
_ECC_ERRORS = 12
#: ``gpu-009`` - script #9 ground truth (node is cordoned, not broken).
_UNSCHEDULABLE_INDEX = 8


@dataclass(frozen=True, slots=True)
class NodeSpec:
    hostname: str
    node_model: str
    switch: str
    rack: str
    gpu_model: str
    gpu_count: int
    status: NodeHealth
    schedulable: bool
    heartbeat_at: datetime
    tags: dict[str, Any]


@dataclass(frozen=True, slots=True)
class TicketSpec:
    ticket_no: str
    hostname: str | None
    script_key: str
    severity: AlertSeverity
    summary: str
    root_cause: str
    tool_name: str
    confidence: float
    iterations: int
    tokens: int
    duration_ms: int
    verdict: ReviewVerdict
    corrected_cause: str | None
    fired_at: datetime


@dataclass(frozen=True, slots=True)
class Fleet:
    seed: int
    nodes: tuple[NodeSpec, ...]
    logs: dict[str, tuple[str, ...]]
    tickets: tuple[TicketSpec, ...]


@dataclass(frozen=True, slots=True)
class SeedReport:
    """Row counts either inserted now or already present (``inserted=False``)."""

    inserted: bool
    nodes: int
    tickets: int
    runs: int
    steps: int
    reviews: int


@dataclass(frozen=True, slots=True)
class TicketCategory:
    key: str
    severity: AlertSeverity
    summary: str
    root_cause: str
    tool_name: str
    node_bound: bool = True


#: The ten recurring ticket classes of the (simulated) fleet.  D43 maps 20 of
#: these onto concrete nodes and adds the five traps; the history here is only
#: meant to look like real operations data.
CATEGORIES: tuple[TicketCategory, ...] = (
    TicketCategory(
        key="gpu_ecc",
        severity=AlertSeverity.CRITICAL,
        summary="GPU ECC 不可纠正错误，训练作业频繁中断",
        root_cause="GPU 显存 ECC 错误（卡 2），需下线更换",
        tool_name="get_node_gpus",
    ),
    TicketCategory(
        key="gpu_oom",
        severity=AlertSeverity.WARNING,
        summary="训练作业显存 OOM 退出",
        root_cause="batch_size 过大导致显存超限",
        tool_name="get_job_logs",
    ),
    TicketCategory(
        key="nccl_timeout",
        severity=AlertSeverity.CRITICAL,
        summary="NCCL all-reduce 超时，多机任务卡死",
        root_cause="IB 链路抖动导致 NCCL 集合通信超时",
        tool_name="get_job_logs",
    ),
    TicketCategory(
        key="node_offline",
        severity=AlertSeverity.CRITICAL,
        summary="节点心跳丢失，scheduler 标记 NotReady",
        root_cause="节点 BMC 自重启，等待上电完成",
        tool_name="get_node_status",
    ),
    TicketCategory(
        key="switch_flap",
        severity=AlertSeverity.CRITICAL,
        summary="同交换机多台节点同时失联",
        root_cause="交换机上联端口抖动，构成网络分区",
        tool_name="get_node_status",
        node_bound=False,
    ),
    TicketCategory(
        key="xid_79",
        severity=AlertSeverity.CRITICAL,
        summary="GPU 报 Xid 79，从总线上掉卡",
        root_cause="GPU PCIe 链路错误，卡已掉线",
        tool_name="get_node_gpus",
    ),
    TicketCategory(
        key="quota_exceeded",
        severity=AlertSeverity.WARNING,
        summary="用户组 GPU 配额用尽，作业持续排队",
        root_cause="用户组 gpu_limit 已打满，需扩容或等待释放",
        tool_name="get_metrics",
        node_bound=False,
    ),
    TicketCategory(
        key="disk_full",
        severity=AlertSeverity.WARNING,
        summary="本地盘写满，容器反复启动失败",
        root_cause="/var/lib/docker 写满，镜像层无法写入",
        tool_name="get_job_logs",
    ),
    TicketCategory(
        key="scheduler_stuck",
        severity=AlertSeverity.WARNING,
        summary="节点长期空闲但调度器不再派活",
        root_cause="节点被打上 unschedulable 标签，调度器拒绝派单",
        tool_name="get_node_status",
    ),
    TicketCategory(
        key="licence_expiry",
        severity=AlertSeverity.INFO,
        summary="调度器许可证即将到期",
        root_cause="License 7 天后到期，需要续期",
        tool_name="get_metrics",
        node_bound=False,
    ),
)


def _build_cards(
    index: int, gpu_count: int, memory_mib: int, status: NodeHealth
) -> list[dict[str, Any]]:
    """Per-card state: an offline node reports every card unreachable."""
    cards: list[dict[str, Any]] = []
    for card in range(gpu_count):
        card_status = "ok"
        ecc_errors = 0
        if status is NodeHealth.OFFLINE:
            card_status = "unreachable"
            memory_used = 0
        else:
            memory_used = (index * 977 + card * 311) % (memory_mib // 2)
            if index == _ECC_NODE_INDEX and card == _ECC_CARD_INDEX:
                card_status = "ecc_error"
                ecc_errors = _ECC_ERRORS
                memory_used = memory_mib // 2
            elif status is NodeHealth.DEGRADED and card == index % gpu_count:
                card_status = "xid_79"
        cards.append(
            {
                "index": card,
                "status": card_status,
                "ecc_errors": ecc_errors,
                "utilization_pct": 0
                if card_status == "unreachable"
                else (index * 13 + card * 7) % 100,
                "memory_used_mib": memory_used,
                "memory_total_mib": memory_mib,
                "temperature_c": 34 + (index * 3 + card) % 25,
            }
        )
    return cards


def _build_nodes(fake: Faker) -> list[NodeSpec]:
    nodes: list[NodeSpec] = []
    for index in range(NODE_COUNT):
        rack_index = index // NODES_PER_RACK
        node_model, gpu_model, gpu_count, memory_mib = _NODE_PROFILES[
            rack_index % len(_NODE_PROFILES)
        ]
        status = _STATUS_OVERRIDES.get(index, NodeHealth.ONLINE)
        nodes.append(
            NodeSpec(
                hostname=f"gpu-{index + 1:03d}",
                node_model=node_model,
                switch=f"sw-{rack_index + 1:02d}",
                rack=f"rack-{chr(ord('a') + rack_index)}",
                gpu_model=gpu_model,
                gpu_count=gpu_count,
                status=status,
                schedulable=index != _UNSCHEDULABLE_INDEX,
                heartbeat_at=BASE_TIME - timedelta(minutes=_HEARTBEAT_LAG_MINUTES[status]),
                tags={
                    "serial": fake.bothify(text="SN-########??").upper(),
                    "owner": fake.name(),
                    "gpus": _build_cards(index, gpu_count, memory_mib, status),
                },
            )
        )
    return nodes


def _log_line(minutes_ago: int, level: str, message: str) -> str:
    timestamp = (BASE_TIME - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"{timestamp} {level:<5} {message}"


def _build_logs(nodes: Sequence[NodeSpec]) -> dict[str, tuple[str, ...]]:
    logs: dict[str, tuple[str, ...]] = {}
    for index, node in enumerate(nodes):
        lines = [
            _log_line(9, "INFO", f"node-agent {node.hostname} heartbeat ok ({node.node_model})"),
            _log_line(
                8,
                "INFO",
                f"sched lease renewed hostname={node.hostname} slots={node.gpu_count}",
            ),
            _log_line(7, "INFO", f"dcgm exporter scrape ok, gpus={node.gpu_count}"),
        ]
        ecc_card = next((card for card in node.tags["gpus"] if card["ecc_errors"]), None)
        if ecc_card is not None:
            lines.append(
                _log_line(
                    6,
                    "ERROR",
                    f"nvidia-smi: ECC error count {ecc_card['ecc_errors']} "
                    f"on GPU {ecc_card['index']} (uncorrectable)",
                )
            )
        if node.status is NodeHealth.DEGRADED:
            broken = next(card for card in node.tags["gpus"] if card["status"] == "xid_79")
            lines.append(
                _log_line(6, "ERROR", f"nvidia-smi: Xid 79 GPU {broken['index']} fell off the bus")
            )
            lines.append(
                _log_line(
                    5, "WARN", f"nvlink bandwidth degraded on {node.hostname}, all-reduce slow"
                )
            )
        if node.status is NodeHealth.OFFLINE:
            lines.append(_log_line(5, "ERROR", "heartbeat missed 3/3 consecutive windows"))
            lines.append(_log_line(4, "ERROR", "ib0 link down - no carrier, running jobs evicted"))
        if not node.schedulable:
            lines.append(
                _log_line(4, "WARN", f"operator cordoned {node.hostname}: unschedulable=true")
            )
        lines.append(_log_line(2, "INFO", f"fs /var/lib/docker usage={40 + (index * 7) % 50}%"))
        lines.append(_log_line(1, "INFO", f"collectd flush ok rows={120 + index}"))
        logs[node.hostname] = tuple(lines)
    return logs


def _build_tickets(nodes: Sequence[NodeSpec]) -> tuple[TicketSpec, ...]:
    tickets: list[TicketSpec] = []
    for index in range(TICKET_COUNT):
        category = CATEGORIES[index % len(CATEGORIES)]
        hostname = nodes[(index * 7) % len(nodes)].hostname if category.node_bound else None
        verdict = ReviewVerdict.WRONG if index % 7 == 0 else ReviewVerdict.CORRECT
        corrected_cause = None
        if verdict is ReviewVerdict.WRONG:
            # A reviewer's correction that disagrees with the model's first guess.
            corrected_cause = CATEGORIES[(index + 3) % len(CATEGORIES)].root_cause
        tickets.append(
            TicketSpec(
                ticket_no=f"HIST-{index + 1:04d}",
                hostname=hostname,
                script_key=category.key,
                severity=category.severity,
                summary=category.summary,
                root_cause=category.root_cause,
                tool_name=category.tool_name,
                confidence=0.70 + (index % 29) * 0.01,
                iterations=2 + index % 5,
                tokens=1800 + (index * 37) % 9000,
                duration_ms=4000 + (index * 131) % 20000,
                verdict=verdict,
                corrected_cause=corrected_cause,
                fired_at=BASE_TIME - timedelta(hours=3 * (index + 1)),
            )
        )
    return tuple(tickets)


def build_fleet(seed: int = SEED) -> Fleet:
    """Build the whole dataset in memory; identical output for an identical seed."""
    Faker.seed(seed)
    fake = Faker("zh_CN")
    nodes = _build_nodes(fake)
    return Fleet(
        seed=seed,
        nodes=tuple(nodes),
        logs=_build_logs(nodes),
        tickets=_build_tickets(nodes),
    )


def _jsonable(value: Any) -> Any:  # noqa: ANN401 - recursive projection
    if is_dataclass(value) and not isinstance(value, type):
        value = asdict(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, NodeHealth | AlertSeverity | ReviewVerdict):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def snapshot(fleet: Fleet) -> dict[str, Any]:
    """JSON-safe, key-sorted projection - the reproducibility fingerprint."""
    return {
        "seed": fleet.seed,
        "nodes": _jsonable(fleet.nodes),
        "logs": _jsonable(fleet.logs),
        "tickets": _jsonable(fleet.tickets),
    }


async def _count(db: AsyncSession, model: type[Base]) -> int:
    return int(await db.scalar(select(func.count()).select_from(model)) or 0)


async def seed_diagnosis_data(db: AsyncSession, *, fleet: Fleet | None = None) -> SeedReport:
    """Insert the mock fleet + history.  A second call is a no-op.

    The check is all-or-nothing (is ``incident_nodes`` empty?) on purpose: the
    dataset is one frozen snapshot, and a half-seeded fleet would silently skew a
    diagnosis run.  The caller owns the commit.
    """
    fleet = fleet or build_fleet()
    node_count = await _count(db, IncidentNode)
    if node_count:
        return SeedReport(
            inserted=False,
            nodes=node_count,
            tickets=await _count(db, Alert),
            runs=await _count(db, DiagnosisRun),
            steps=await _count(db, DiagnosisStep),
            reviews=await _count(db, DiagnosisReview),
        )

    db.add_all(
        [
            IncidentNode(
                hostname=node.hostname,
                node_model=node.node_model,
                switch=node.switch,
                rack=node.rack,
                gpu_model=node.gpu_model,
                gpu_count=node.gpu_count,
                status=node.status.value,
                schedulable=node.schedulable,
                heartbeat_at=node.heartbeat_at,
                tags={**node.tags, "logs": list(fleet.logs[node.hostname])},
                created_at=BASE_TIME,
            )
            for node in fleet.nodes
        ]
    )
    await db.flush()
    rows = (await db.execute(select(IncidentNode.hostname, IncidentNode.id))).all()
    node_ids = dict(rows)

    alerts = [
        Alert(
            node_id=node_ids.get(ticket.hostname) if ticket.hostname else None,
            script_key=ticket.script_key,
            severity=ticket.severity.value,
            summary=ticket.summary,
            status=AlertStatus.RESOLVED.value,
            fired_at=ticket.fired_at,
            created_at=ticket.fired_at,
        )
        for ticket in fleet.tickets
    ]
    db.add_all(alerts)
    await db.flush()

    runs: list[tuple[DiagnosisRun, TicketSpec]] = []
    for ticket, alert in zip(fleet.tickets, alerts, strict=True):
        run = DiagnosisRun(
            alert_id=alert.id,
            status=RunStatus.SUCCEEDED.value,
            root_cause=ticket.root_cause,
            confidence=ticket.confidence,
            iterations=ticket.iterations,
            tokens=ticket.tokens,
            trace_id=f"hist-{ticket.ticket_no.lower()}",
            duration_ms=ticket.duration_ms,
            created_at=ticket.fired_at + timedelta(seconds=30),
            updated_at=ticket.fired_at + timedelta(seconds=30 + ticket.duration_ms // 1000),
        )
        db.add(run)
        runs.append((run, ticket))
    await db.flush()

    steps = 0
    for index, (run, ticket) in enumerate(runs):
        tool_args: dict[str, Any] = (
            {"hostname": ticket.hostname} if ticket.hostname else {"window": 60}
        )
        start = ticket.fired_at + timedelta(seconds=31)
        db.add_all(
            [
                DiagnosisStep(
                    run_id=run.id,
                    seq=1,
                    node=StepPhase.PLAN.value,
                    result={"plan": [ticket.tool_name]},
                    latency_ms=0,
                    created_at=start,
                ),
                DiagnosisStep(
                    run_id=run.id,
                    seq=2,
                    node=StepPhase.TOOL.value,
                    tool_name=ticket.tool_name,
                    args=tool_args,
                    result={"ok": True, "hostname": ticket.hostname},
                    latency_ms=12 + index % 40,
                    created_at=start + timedelta(seconds=1),
                ),
                DiagnosisStep(
                    run_id=run.id,
                    seq=3,
                    node=StepPhase.REPORT.value,
                    result={"root_cause": ticket.root_cause, "evidence_ids": [2]},
                    latency_ms=0,
                    created_at=start + timedelta(seconds=2),
                ),
            ]
        )
        db.add(
            DiagnosisReview(
                run_id=run.id,
                verdict=ticket.verdict.value,
                corrected_cause=ticket.corrected_cause,
                reviewer_id=None,
                created_at=ticket.fired_at + timedelta(minutes=45 + index % 30),
            )
        )
        steps += 3

    return SeedReport(
        inserted=True,
        nodes=len(fleet.nodes),
        tickets=len(fleet.tickets),
        runs=len(fleet.tickets),
        steps=steps,
        reviews=len(fleet.tickets),
    )


async def main(seed: int = SEED) -> None:  # pragma: no cover - CLI glue
    async with async_session_factory() as db:
        report = await seed_diagnosis_data(db, fleet=build_fleet(seed))
        await db.commit()
    state = "seeded" if report.inserted else "already seeded, skipped"
    print(
        f"{state}: nodes={report.nodes} tickets={report.tickets} runs={report.runs} "
        f"steps={report.steps} reviews={report.reviews} (seed={seed})"
    )


if __name__ == "__main__":  # pragma: no cover - CLI glue
    parser = argparse.ArgumentParser(description="Seed the deterministic diagnosis mock fleet")
    parser.add_argument("--seed", type=int, default=SEED)
    asyncio.run(main(parser.parse_args().seed))
