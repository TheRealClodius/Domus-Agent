#!/usr/bin/env python3
"""Watch builder logs in real-time.

Usage (in a separate terminal):
    python scripts/watch-builder.py

Tails logs/builder.jsonl and pretty-prints builder events.
"""

import json
import os
import time
import sys

# ANSI colors
DIM = "\033[2m"
RESET = "\033[0m"
BOLD = "\033[1m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
CYAN = "\033[36m"
MAGENTA = "\033[35m"

LOG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs", "builder.jsonl")


def format_line(data: dict) -> str | None:
    msg = data.get("message", "")
    eid = data.get("entity_id", "?")[:8]
    turn = data.get("turn", "")

    if msg == "builder_start":
        spec = data.get("spec", "")
        return f"\n{BOLD}{GREEN}▶ BUILD START{RESET}  entity={eid}\n  spec: {DIM}{spec}{RESET}"

    if msg == "builder_turn_start":
        return f"  {DIM}── turn {turn} ──{RESET}"

    if msg == "builder_model_text":
        text = data.get("text", "")
        return f"  {DIM}💭 {text}{RESET}"

    if msg == "builder_tool_call":
        tool = data.get("tool", "?")
        ok = data.get("result_ok", False)
        status = f"{GREEN}✓{RESET}" if ok else f"{RED}✗{RESET}"

        detail = ""
        if tool == "add_block":
            bt = data.get("block_type", "?")
            bi = data.get("block_id", "?")
            detail = f"  {CYAN}{bt}{RESET} id={bi}"
        elif tool == "update_block":
            detail = f"  id={data.get('block_id', '?')}"
        elif tool == "set_tools_schema":
            detail = f"  {data.get('tool_count', 0)} tools"

        error = data.get("error", "")
        if error:
            detail += f"  {RED}{error}{RESET}"

        return f"  {status} {MAGENTA}{tool}{RESET}{detail}"

    if msg == "builder_auto_finish":
        reason = data.get("stop_reason", "?")
        return f"  {YELLOW}⏎ auto-finish{RESET} (stop_reason={reason})"

    if msg == "builder_done":
        return f"{BOLD}{GREEN}✔ BUILD DONE{RESET}  entity={eid}\n"

    if msg == "builder_error":
        error = data.get("error", "?")
        return f"{BOLD}{RED}✗ BUILD ERROR{RESET}  entity={eid}  {error}\n"

    if msg == "builder_task_crashed":
        error = data.get("error", "?")
        return f"{BOLD}{RED}💥 BUILDER CRASHED{RESET}  entity={eid}  {error}\n"

    # Catch-all for any builder log we didn't format specifically
    return f"  {DIM}{msg} {json.dumps({k: v for k, v in data.items() if k not in ('timestamp', 'level', 'message', 'logger')})}{RESET}"


def tail_file(path: str):
    """Tail a file, yielding new lines as they appear."""
    # Wait for file to exist
    while not os.path.exists(path):
        time.sleep(0.5)

    with open(path, "r") as f:
        # Seek to end
        f.seek(0, 2)
        while True:
            line = f.readline()
            if line:
                yield line.strip()
            else:
                time.sleep(0.1)


def main():
    print(f"{BOLD}Watching builder logs{RESET} ({LOG_PATH})")
    print(f"{DIM}Waiting for builds...{RESET}\n")

    for line in tail_file(LOG_PATH):
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        formatted = format_line(data)
        if formatted:
            print(formatted, flush=True)


if __name__ == "__main__":
    main()
