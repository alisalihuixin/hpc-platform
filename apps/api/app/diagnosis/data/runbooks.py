"""Deterministic runbook knowledge base behind ``search_runbooks`` (D45).

Eight entries cover the recurring ticket classes; titles are the natural key so
re-running the seed only fills in what is missing.  Bodies stay short on purpose:
they are prompt material, not a wiki - M8 replaces the ILIKE lookup with RAG but
keeps this table as the source documents.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import async_session_factory
from app.models.diagnosis import Runbook


@dataclass(frozen=True, slots=True)
class RunbookSpec:
    title: str
    tags: str
    body: str


RUNBOOKS: tuple[RunbookSpec, ...] = (
    RunbookSpec(
        title="GPU ECC 错误处置",
        tags="gpu,ecc,换卡",
        body=(
            "1. nvidia-smi -q -d ECC 确认 correctable / uncorrectable 计数；"
            "2. uncorrectable 非零且递增时，先 drain 节点再报修换卡；"
            "3. 仅 correctable 且不增长可继续观察，并记录到维保工单。"
        ),
    ),
    RunbookSpec(
        title="Xid 79 掉卡处置",
        tags="gpu,xid,nvlink,pcie",
        body=(
            "1. 收集 dmesg 与 nvidia-smi -q 中的 Xid、PCIe 链路速率；"
            "2. Xid 79 表示 GPU 从总线掉线，重启无效则换卡；"
            "3. 同机多卡连续出现时检查 NVLink 与 PCIe switch 温度。"
        ),
    ),
    RunbookSpec(
        title="节点心跳丢失（BMC 重启）",
        tags="node,offline,bmc,心跳",
        body=(
            "1. 先看带外 BMC 是否可登录，判断是否整机掉电；"
            "2. BMC 自重启通常在 5-10 分钟内恢复，期间不要重复下发重启；"
            "3. 超过 30 分钟未恢复，转硬件值班并检查机架 PDU。"
        ),
    ),
    RunbookSpec(
        title="节点 cordon / drain 操作",
        tags="cordon,drain,schedulable,维护",
        body=(
            "1. 维护前 cordon 节点（schedulable=false），等待运行中作业自然结束；"
            "2. drain 会驱逐作业，需提前通知用户；"
            "3. 维护完成后解除 cordon，并确认调度器重新派单。"
        ),
    ),
    RunbookSpec(
        title="NCCL / IB 超时排查",
        tags="nccl,ib,network,超时",
        body=(
            "1. 对比同交换机 5 台节点的报错时间，多点同时超时指向网络；"
            "2. ibstat / ibstatus 检查端口速率与误码计数；"
            "3. 单节点超时查该节点 GPU 与驱动，多点超时查交换机上联。"
        ),
    ),
    RunbookSpec(
        title="配额不足与扩容",
        tags="quota,扩容,排队",
        body=(
            "1. 查用户组 gpu_used 与 gpu_limit，确认是否真的打满；"
            "2. 配额有余量却排队时，检查空闲节点是否被 cordon；"
            "3. 扩容需走资源组审批，临时可借用空闲池。"
        ),
    ),
    RunbookSpec(
        title="磁盘写满清理",
        tags="disk,docker,清理",
        body=(
            "1. du -sh /var/lib/docker/* 定位镜像层或容器日志；"
            "2. docker system prune -a 前先确认无运行中作业依赖旧镜像；"
            "3. 训练作业写盘量用 du 与日志交叉验证，避免误判为作业写满。"
        ),
    ),
    RunbookSpec(
        title="调度器 License 续期",
        tags="license,续期,scheduler",
        body=(
            "1. 在 License 服务器确认到期时间与授权核数；"
            "2. 提前 7 天提交续期申请，避免调度器进入只读模式；"
            "3. 续期后重启调度器并核对可用核数。"
        ),
    ),
)


async def seed_runbooks(db: AsyncSession) -> int:
    """Insert missing runbooks by title; returns how many were added."""
    known = set((await db.scalars(select(Runbook.title))).all())
    added = 0
    for spec in RUNBOOKS:
        if spec.title in known:
            continue
        db.add(Runbook(title=spec.title, tags=spec.tags, body=spec.body))
        added += 1
    await db.flush()
    return added


async def main() -> None:  # pragma: no cover - CLI glue
    async with async_session_factory() as db:
        added = await seed_runbooks(db)
        await db.commit()
    print(f"runbooks: added={added} total={len(RUNBOOKS)}")


if __name__ == "__main__":  # pragma: no cover - CLI glue
    asyncio.run(main())
