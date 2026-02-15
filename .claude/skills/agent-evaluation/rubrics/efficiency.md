# Efficiency Rubric

## What It Measures
Did the agent solve the task within reasonable resource bounds?

## Scoring (1-5)

| Score | Criteria |
|-------|----------|
| 5 | Well within all budgets. Minimal token usage. Direct path to solution. |
| 4 | Within budgets with minor inefficiency. Slight excess in one dimension. |
| 3 | At budget limits. Notable inefficiency in approach. |
| 2 | Exceeds budgets in multiple dimensions. Wasteful approach. |
| 1 | Massively exceeds budgets. Circular reasoning. Excessive tool calls. |

## Metrics

| Metric | How to Measure | Typical Budget |
|--------|---------------|----------------|
| **Token usage** | Input + output tokens total | Varies by task complexity |
| **Tool call count** | Total tool invocations | 10-20 for simple, 30-50 for complex |
| **Step count** | Agent turns / reasoning steps | 5-15 for simple, 15-30 for complex |
| **Wall-clock time** | Elapsed seconds to completion | Task-dependent |
| **API cost** | Estimated $ based on token pricing | Set per task type |
| **Convergence** | Did agent reach goal, or give up / loop? | Boolean |

## Budget Definition (in Eval Contract)

```json
{
  "budgets": {
    "max_steps": 15,
    "max_tool_calls": 20,
    "max_tokens": 50000,
    "max_wall_clock_seconds": 300,
    "max_cost_usd": 0.50
  }
}
```

Evaluations that exceed budgets automatically cap at score 2.

## Automated Scoring

```python
def score_efficiency(trace: dict, budgets: dict) -> dict:
    actual = {
        "steps": count_steps(trace),
        "tool_calls": count_tool_calls(trace),
        "tokens": count_tokens(trace),
        "wall_clock_seconds": trace["duration_seconds"],
        "cost_usd": estimate_cost(trace),
    }

    over_budget = {}
    for key, limit in budgets.items():
        metric_key = key.replace("max_", "")
        if metric_key in actual and actual[metric_key] > limit:
            over_budget[metric_key] = {
                "actual": actual[metric_key],
                "limit": limit,
                "ratio": actual[metric_key] / limit,
            }

    if not over_budget:
        # Check how efficiently within budget
        avg_utilization = sum(
            actual.get(k.replace("max_", ""), 0) / v
            for k, v in budgets.items()
            if v > 0
        ) / len(budgets)

        if avg_utilization < 0.5:
            score = 5
        elif avg_utilization < 0.8:
            score = 4
        else:
            score = 3
    else:
        score = 2 if len(over_budget) <= 1 else 1

    return {"score": score, "actual": actual, "over_budget": over_budget}
```

## Tracking Over Time

Track efficiency metrics per task type across model versions and prompt iterations to detect regressions:

```
task_type: code_generation
model: claude-sonnet-4-5-20250929
avg_tokens: 12,400 (±2,100)
avg_tool_calls: 8.3 (±1.2)
avg_cost: $0.08
```
