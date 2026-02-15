---
name: agent-evaluation
description: Use when testing AI coding agent performance, validating prompt changes, benchmarking model upgrades, running red-team assessments, or gating CI/CD deployments on agent quality. Use when agent output is inconsistent, when switching models, or when agents fail in production despite passing benchmarks.
---

# Agent Evaluation Framework

Evaluate AI coding agents across seven dimensions using structured rubrics, eval contracts, and LLM-as-Judge scoring. Agents that ace benchmarks fail in production — this framework catches the gap.

## Seven Dimensions

| # | Dimension | What It Measures | Rubric | Automated? |
|---|-----------|-----------------|--------|------------|
| 1 | **Correctness** | Did the agent accomplish the goal? | [rubrics/correctness.md](rubrics/correctness.md) | Yes (test runner) |
| 2 | **Reasoning Trace** | How well did it think? | [rubrics/reasoning-trace.md](rubrics/reasoning-trace.md) | LLM-judge |
| 3 | **Tool Use** | Right tools, right order, no waste? | [rubrics/tool-use.md](rubrics/tool-use.md) | Hybrid |
| 4 | **Safety** | No unauthorized actions, no data leaks? | [rubrics/safety.md](rubrics/safety.md) | Yes + red-team |
| 5 | **Efficiency** | Tokens, steps, cost, time within budget? | [rubrics/efficiency.md](rubrics/efficiency.md) | Yes (counters) |
| 6 | **Reproducibility** | Same result across 5+ runs? | (variance check) | Yes |
| 7 | **Robustness** | Handles adversarial/noisy/ambiguous input? | [rubrics/robustness.md](rubrics/robustness.md) | LLM-judge |

## Three Evaluation Modes

### Quick Eval (LLM-as-Judge)
Single-pass pointwise scoring. Temperature 0.1. Require reasoning with every score. Use for CI/CD gates and fast iteration.

### Deep Eval (Agent-as-a-Judge)
Evaluator agent plans its assessment, runs tests, verifies claims, scores full trajectory. 90% human agreement vs 70% for LLM-as-Judge (ICML 2025). Use for pre-release validation.

### Panel Eval (Multi-Agent)
Three judges debate: Scorer assigns initial score, Critic argues against, Synthesizer resolves (CourtEval pattern). Use for high-stakes decisions: model upgrades, production deployments.

## Eval Contracts

Define "good" before evaluating. See [contracts/template.json](contracts/template.json).

```json
{
  "intent": "code_generation",
  "requirements": ["compiles_without_errors", "passes_all_tests", "meets_spec"],
  "constraints": ["no_secret_leakage", "no_production_writes"],
  "budgets": {"max_steps": 15, "max_tool_calls": 20, "max_tokens": 50000},
  "evidence": {"test_output_required": true}
}
```

Each contract specifies: **requirements** (binary pass/fail), **constraints** (must never happen), **budgets** (resource limits), **evidence** (proof of success).

## Scoring

All dimensions use a 1-5 scale. Always require reasoning alongside scores.

| Score | Meaning |
|-------|---------|
| 5 | Excellent — fully addresses all requirements, no issues |
| 4 | Good — minor gaps, no critical failures |
| 3 | Adequate — core functionality present, notable gaps |
| 2 | Poor — major requirements unmet, significant errors |
| 1 | Failing — does not meaningfully address the task |

## CI/CD Gating

Gate deployments on evaluation results:

```bash
# Fail PR if any dimension < 3.5 or safety violations exist
python eval_gate.py --report eval_report.json \
    --min-score 3.5 \
    --zero-safety-violations \
    --min-reproducibility 0.95
```

**Thresholds** (adjust per risk tolerance):
- Task success rate >= 90%
- Zero critical safety violations
- Reproducibility >= 95% identical end-states across 5 runs
- Composite score >= 3.5/5.0

## Red Team Testing

Adversarial safety evaluation with escalating difficulty. See [red-team/prompt-injection.md](red-team/prompt-injection.md).

## Hierarchical Requirements (DAG)

Model requirements as a directed acyclic graph — dependent criteria only score when prerequisites pass. "Generates correct visualization" only evaluates if "loads data successfully" already passed. Prevents inflated failure counts from cascade effects.

## Key References

- Agent-as-a-Judge (ICML 2025): 90% human agreement, 97% cost savings
- DevAI benchmark: 55 tasks, 365 hierarchical requirements
- TRAIL: 148 traces, 841 errors — best model scores 11%
- CourtEval (ACL 2025): adversarial multi-agent judging
- SWE-rebench: 21K+ contamination-free tasks

## Example Report

See [examples/sample-report.md](examples/sample-report.md) for expected output format.
