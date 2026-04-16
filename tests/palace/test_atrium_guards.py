"""Blacklist trigger (防铁锈 firewall) tests."""
from __future__ import annotations

import uuid

import apsw
import pytest


PATTERNS = [
    "不可用", "未能找到", "服务中断", "超时",
    "工具报错", "无法访问", "操作失败", "连接失败",
]


@pytest.mark.parametrize("pat", PATTERNS)
async def test_blacklist_trigger_rejects(store, pat):
    with pytest.raises(apsw.Error) as ei:
        await store.insert_atrium_entry(
            id=str(uuid.uuid4()),
            entry_type="rule",
            content=f"前缀{pat}后缀",
            source_type="explicit",
            status="active",
        )
    assert "blacklist" in str(ei.value).lower()


async def test_blacklist_allows_clean(store):
    aid = str(uuid.uuid4())
    await store.insert_atrium_entry(
        id=aid,
        entry_type="preference",
        content="用户偏好简洁直接回答",
        source_type="explicit",
        status="active",
    )
    entry = await store.get_atrium_entry(aid)
    assert entry is not None
    assert entry["status"] == "active"
    assert entry["content"] == "用户偏好简洁直接回答"


async def test_atrium_status_transition(store):
    aid = str(uuid.uuid4())
    await store.insert_atrium_entry(
        id=aid, entry_type="rule",
        content="晚上 22 点后只短回复",
        source_type="explicit", status="pending",
    )
    await store.update_atrium_status(aid, "active")
    entry = await store.get_atrium_entry(aid)
    assert entry["status"] == "active"
    assert entry["approved_at"] is not None


async def test_list_atrium_by_status(store):
    for i, status in enumerate(["active", "pending", "rejected"]):
        await store.insert_atrium_entry(
            id=f"atrium-{i}",
            entry_type="rule",
            content=f"rule {i}",
            source_type="explicit",
            status=status,
        )
    active = await store.list_atrium_entries(status="active")
    assert len(active) == 1
    assert active[0]["id"] == "atrium-0"
