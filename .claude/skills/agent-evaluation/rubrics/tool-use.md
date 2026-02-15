# Tool Use Rubric

## What It Measures
Did the agent pick the right tools, use them correctly, and avoid waste?

## Scoring (1-5)

| Score | Criteria |
|-------|----------|
| 5 | Optimal tool selection. Well-formed inputs. No redundant calls. Handles tool errors gracefully. |
| 4 | Good tool selection with minor suboptimalities. Occasional unnecessary call. Handles most errors. |
| 3 | Adequate tool selection. Some wrong tools chosen. Notable redundancy. Partial error handling. |
| 2 | Frequent wrong tool selection. Many redundant calls. Poor error handling. Context lost between calls. |
| 1 | Consistently wrong tools. Excessive redundancy. No error handling. Complete context loss. |

## Failure Taxonomy

### Type I: Tool Selection Error
Agent picks the wrong tool for the job (e.g., using Bash to read a file when Read tool exists).

### Type II: Input Formation Error
Agent provides malformed or contextually inappropriate inputs to the correct tool.

### Type III: Context Drift
Agent loses track of accumulated state across tool calls. Forgets what it already learned.

### Type IV: Cascade Failure
A tool error propagates and derails subsequent steps. Agent doesn't recognize the cascade.

### Type V: Redundancy
Agent makes the same or equivalent call multiple times without reason.

## Metrics

- **Tool selection correctness**: % of calls where the right tool was chosen
- **Semantic alignment**: Were inputs well-formed and contextually appropriate?
- **Cascade failure count**: How many tool errors propagated to derail subsequent steps?
- **Context retention**: Did the agent maintain state across calls?
- **Redundancy ratio**: Unnecessary calls / total calls
- **Error recovery rate**: % of tool failures followed by successful adaptation

## Automated Checks

```python
def analyze_tool_use(trace: dict) -> dict:
    calls = extract_tool_calls(trace)

    metrics = {
        "total_calls": len(calls),
        "unique_calls": len(set((c["tool"], c["input_hash"]) for c in calls)),
        "redundant_calls": 0,
        "failed_calls": 0,
        "recovered_after_failure": 0,
        "cascade_failures": 0,
    }

    # Detect redundancy
    seen = set()
    for call in calls:
        key = (call["tool"], call["input_hash"])
        if key in seen:
            metrics["redundant_calls"] += 1
        seen.add(key)

    # Detect cascade failures
    for i, call in enumerate(calls):
        if call["status"] == "error":
            metrics["failed_calls"] += 1
            if i + 1 < len(calls) and calls[i + 1]["status"] == "error":
                metrics["cascade_failures"] += 1
            elif i + 1 < len(calls):
                metrics["recovered_after_failure"] += 1

    metrics["redundancy_ratio"] = metrics["redundant_calls"] / max(metrics["total_calls"], 1)
    return metrics
```

## Logging Requirements

Log every tool call with:
- Timestamp
- Tool name
- Input (or hash for large inputs)
- Output status (success/error)
- Output hash
- Token cost
- Latency
