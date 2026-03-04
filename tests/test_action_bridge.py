"""Tests for agent.action_bridge — ActionBridge, PendingAction, and registry."""

import asyncio

import pytest

from agent.action_bridge import (
    ActionBridge,
    get_bridge,
    register_bridge,
    unregister_bridge,
)


@pytest.mark.asyncio
async def test_create_and_resolve():
    """Create an action, resolve it concurrently, verify future returns the result."""
    bridge = ActionBridge()
    pa = bridge.create_action("create_entity", {"type": "note"})
    assert pa.action_id.startswith("act_")

    result_data = {"success": True, "result": {"id": "ent_123"}}

    async def resolve_soon():
        await asyncio.sleep(0.01)
        bridge.resolve(pa.action_id, result_data)

    asyncio.create_task(resolve_soon())
    result = await bridge.wait_for_result(pa.action_id, timeout=1.0)
    assert result == result_data


@pytest.mark.asyncio
async def test_timeout():
    """Unresolved action should return timeout error."""
    bridge = ActionBridge()
    pa = bridge.create_action("update_entity", {"id": "ent_1"})

    result = await bridge.wait_for_result(pa.action_id, timeout=0.05)
    assert result["error"] == "ui_action_timeout"
    assert result["action_id"] == pa.action_id


@pytest.mark.asyncio
async def test_resolve_unknown_id():
    """Resolving an unknown action_id returns False."""
    bridge = ActionBridge()
    assert bridge.resolve("act_nonexistent", {"ok": True}) is False


@pytest.mark.asyncio
async def test_double_resolve():
    """Second resolve on the same action_id returns False."""
    bridge = ActionBridge()
    pa = bridge.create_action("create_entity", {"type": "image"})

    assert bridge.resolve(pa.action_id, {"success": True}) is True
    assert bridge.resolve(pa.action_id, {"success": True}) is False


def test_registry_lifecycle():
    """Register, get, unregister, get returns None."""
    bridge = ActionBridge()
    register_bridge("space_1", "user_1", bridge)
    assert get_bridge("space_1", "user_1") is bridge

    unregister_bridge("space_1", "user_1")
    assert get_bridge("space_1", "user_1") is None


def test_registry_unregister_missing():
    """Unregistering a non-existent bridge doesn't raise."""
    unregister_bridge("space_missing", "user_missing")


@pytest.mark.asyncio
async def test_wait_for_unknown_action():
    """Waiting for an unknown action_id returns error immediately."""
    bridge = ActionBridge()
    result = await bridge.wait_for_result("act_doesnt_exist", timeout=1.0)
    assert result["error"] == "unknown_action"
