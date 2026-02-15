# Reasoning Trace Quality Rubric

## What It Measures
How well did the agent think? Evaluates the problem-solving trajectory, not just the final output.

## Scoring (1-5)

| Score | Criteria |
|-------|----------|
| 5 | Coherent plan formulated before acting. Each step logically follows. Adapts when steps fail. No hallucinations. |
| 4 | Good planning with minor gaps. Mostly coherent steps. Recovers from most failures. Rare hallucinations. |
| 3 | Some planning evident. Occasional incoherent steps. Partial recovery from failures. |
| 2 | Minimal planning. Frequently incoherent steps. Repeats same mistakes. Multiple hallucinations. |
| 1 | No planning. Random actions. No adaptation. Frequent hallucinations. |

## Sub-Dimensions

### Plan Quality
Does the agent formulate a strategy before acting?
- Identifies sub-tasks
- Sequences them logically
- Anticipates dependencies

### Step Coherence
Does each step logically follow from previous observations?
- References previous outputs
- Builds on accumulated knowledge
- Doesn't contradict earlier findings

### Adaptivity
When a step fails, does the agent adjust?
- Recognizes failure
- Tries alternative approach (not same action repeated)
- Learns from error information

### Hallucination Detection
Does the agent reference things that don't exist?
- Files that aren't in the repo
- APIs that don't exist
- Tool capabilities it doesn't have
- Made-up error messages or outputs

## Evidence Bank Pattern

Maintain a running evidence bank during evaluation that accumulates verified facts from each reasoning step. When scoring later steps, the judge has access to what was already verified:

```
Step 1: Agent reads file → Evidence: file exists, contains X
Step 2: Agent claims "based on the schema in file Y" → Check: did agent actually read file Y? Is there a schema?
Step 3: Agent modifies code → Check: is modification consistent with evidence from steps 1-2?
```

## Judge Prompt

```
You are evaluating the reasoning quality of an AI coding agent's problem-solving trajectory.

TASK:
{task_description}

AGENT TRACE (thought → action → observation sequence):
{trace}

Evaluate each sub-dimension independently:
1. Plan Quality (1-5): Did the agent plan before acting?
2. Step Coherence (1-5): Does each step follow logically?
3. Adaptivity (1-5): Did the agent adjust when things went wrong?
4. Hallucination (1-5): Did the agent reference non-existent things?

For each sub-dimension, provide reasoning BEFORE the score.

Overall Reasoning Trace Score: [average of sub-dimensions, rounded]
```

Temperature: 0.1.
