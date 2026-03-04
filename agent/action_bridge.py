"""Action bridge — async Future-based bridge for UI action mirroring.

When UI_ACTION_MIRRORING is enabled, the agent emits ui_action SSE events
instead of writing visible entities directly. The frontend executes the action
through its UI state machine and POSTs the result back to /agent/action-result,
which resolves the corresponding Future so the agent loop can continue.
"""

import asyncio
import time
from dataclasses import dataclass, field
from uuid import uuid4


@dataclass
class PendingAction:
    """A ui_action waiting for the frontend to respond."""

    action_id: str
    action: str
    params: dict
    future: asyncio.Future
    created_at: float = field(default_factory=time.monotonic)


class ActionBridge:
    """One instance per agent turn. Maps action_id → Future."""

    def __init__(self):
        self._pending: dict[str, PendingAction] = {}

    def create_action(self, action: str, params: dict) -> PendingAction:
        action_id = f"act_{uuid4().hex[:12]}"
        future = asyncio.get_running_loop().create_future()
        pa = PendingAction(
            action_id=action_id,
            action=action,
            params=params,
            future=future,
        )
        self._pending[action_id] = pa
        return pa

    def resolve(self, action_id: str, result: dict) -> bool:
        pa = self._pending.pop(action_id, None)
        if pa is None or pa.future.done():
            return False
        pa.future.set_result(result)
        return True

    def cancel(self, action_id: str) -> None:
        """Remove a pending action without resolving it (e.g. on timeout)."""
        self._pending.pop(action_id, None)

    async def wait_for_result(self, action_id: str, timeout: float = 15.0) -> dict:
        pa = self._pending.get(action_id)
        if pa is None:
            return {"error": "unknown_action", "action_id": action_id}
        try:
            return await asyncio.wait_for(pa.future, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(action_id, None)
            return {"error": "ui_action_timeout", "action_id": action_id}


# ---------------------------------------------------------------------------
# Module-level registry — one bridge active per (space_id, user_id)
# ---------------------------------------------------------------------------

_active_bridges: dict[tuple[str, str], ActionBridge] = {}


def register_bridge(space_id: str, user_id: str, bridge: ActionBridge) -> None:
    _active_bridges[(space_id, user_id)] = bridge


def unregister_bridge(space_id: str, user_id: str) -> None:
    _active_bridges.pop((space_id, user_id), None)


def get_bridge(space_id: str, user_id: str) -> ActionBridge | None:
    return _active_bridges.get((space_id, user_id))
