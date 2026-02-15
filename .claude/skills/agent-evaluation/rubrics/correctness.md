# Correctness Rubric

## What It Measures
Did the agent accomplish the stated goal? Does the output meet requirements?

## Scoring (1-5)

| Score | Criteria |
|-------|----------|
| 5 | All requirements met. Code compiles, all tests pass, matches spec exactly. No manual fixes needed. |
| 4 | Most requirements met. Minor gaps (e.g., missing edge case, incomplete docstring) but functionally correct. |
| 3 | Core functionality present but notable gaps. May need manual intervention for secondary requirements. |
| 2 | Partially addresses task. Major requirements unmet. Significant errors or incomplete implementation. |
| 1 | Does not meaningfully address the task. Critical errors, wrong approach, or no output. |

## Metrics

- **Task success rate**: % of runs reaching defined end state
- **Requirements satisfaction**: Use hierarchical DAG — dependent requirements only score when prerequisites pass
- **Error amplification**: Frequency of early mistakes causing cascading downstream failures
- **Partial credit**: Score requirements independently, then aggregate

## Hierarchical Requirements (DAG)

Structure requirements so dependent criteria only count when prerequisites pass:

```
compiles_without_errors
├── passes_unit_tests
│   ├── handles_edge_cases
│   └── meets_performance_requirements
├── follows_coding_standards
└── includes_documentation
```

If `compiles_without_errors` fails, all children automatically score 0. This prevents inflated failure counts.

## Automated Verification

```python
def score_correctness(trace: dict, contract: dict) -> dict:
    results = {}
    for req in contract["requirements"]:
        if req == "compiles_without_errors":
            results[req] = check_compilation(trace)
        elif req == "passes_all_tests":
            results[req] = check_test_output(trace)
        elif req == "meets_spec":
            results[req] = None  # Requires LLM-judge

    # Apply DAG: zero out children of failed parents
    results = apply_dag_scoring(results, contract.get("requirement_dag"))

    passed = sum(1 for v in results.values() if v is True)
    total = len(results)

    if passed == total:
        return {"score": 5, "passed": passed, "total": total}
    elif passed >= total * 0.8:
        return {"score": 4, "passed": passed, "total": total}
    elif passed >= total * 0.5:
        return {"score": 3, "passed": passed, "total": total}
    elif passed > 0:
        return {"score": 2, "passed": passed, "total": total}
    else:
        return {"score": 1, "passed": 0, "total": total}
```

## Judge Prompt (for "meets_spec" and subjective criteria)

```
You are evaluating whether an AI coding agent's output meets the specification.

SPECIFICATION:
{spec}

AGENT OUTPUT:
{output}

Score on a 1-5 scale. You MUST provide reasoning before your score.

Reasoning: [your analysis]
Score: [1-5]
```

Temperature: 0.1. Always require reasoning before score.
