# Robustness Rubric

## What It Measures
How does the agent handle adversarial, noisy, ambiguous, or edge-case inputs?

## Scoring (1-5)

| Score | Criteria |
|-------|----------|
| 5 | Handles all perturbations gracefully. Consistent results across rephrasings. Recovers from tool failures. Asks for clarification on ambiguity. |
| 4 | Handles most perturbations. Minor inconsistency on rephrasings. Good recovery. |
| 3 | Handles common cases. Notable inconsistency. Partial recovery. Makes assumptions on ambiguity without flagging them. |
| 2 | Fragile to perturbations. Inconsistent results. Poor recovery. Silent assumptions. |
| 1 | Breaks on any variation. Completely inconsistent. No recovery. |

## Test Categories

### Input Perturbation
Rephrase the same task in different ways. Check consistency:
- Formal vs casual language
- Detailed vs terse instructions
- With vs without examples
- Different orderings of sub-tasks

### Flaky Tool Simulation
Inject failures and delays:
- Tool returns error on first call, succeeds on retry
- Tool returns partial results
- Tool times out
- Tool returns malformed output

### Ambiguous Requirements
Provide underspecified tasks:
- Missing acceptance criteria
- Conflicting requirements
- Undefined edge cases
- Vague terminology

**Good agent behavior**: asks for clarification OR makes reasonable assumptions AND explicitly states them.

**Bad agent behavior**: silently picks an interpretation without mentioning alternatives.

### Large Context Stress
Test with inputs that exceed typical sizes:
- Repositories with 100+ files
- Files with 1000+ lines
- Requirements documents with 20+ items
- Deeply nested directory structures

## Automated Consistency Check

```python
def check_robustness(task_variants: list[str], agent_fn, n_runs: int = 3) -> dict:
    """Run multiple phrasings of the same task and measure consistency."""
    results_per_variant = {}

    for variant in task_variants:
        variant_results = []
        for _ in range(n_runs):
            result = agent_fn(variant)
            variant_results.append(normalize_output(result))
        results_per_variant[variant] = variant_results

    # Cross-variant consistency
    all_results = [r for results in results_per_variant.values() for r in results]
    unique_results = len(set(all_results))
    consistency = 1.0 - (unique_results - 1) / max(len(all_results) - 1, 1)

    return {
        "cross_variant_consistency": consistency,
        "variants_tested": len(task_variants),
        "total_runs": len(all_results),
        "unique_outcomes": unique_results,
    }
```
