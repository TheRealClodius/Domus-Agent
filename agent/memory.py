"""Memory compaction — summarize old turns, extract facts and edges via Opus."""

import asyncio
import json
import re

import config as cfg
from agent.logging import get_logger

logger = get_logger("agent.memory")

# Patterns that should never appear in a stored fact.
_PII_PATTERNS = [
    re.compile(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}'),  # email
    re.compile(r'(\+?[\d\s\-().]{7,15}\d)'),                             # phone
]

def _contains_pii(text: str) -> bool:
    return any(p.search(text) for p in _PII_PATTERNS)

_COMPACTION_SYSTEM = """\
You are a memory compactor for a personal AI assistant. Summarize conversation history and extract structured knowledge.
Return valid JSON only — no markdown, no code fences, no explanation.

Schema:
{
  "summary": "string — narrative summary of the conversation segment",
  "facts": [
    {
      "content": "string — a fact about the user or their preferences",
      "confidence": 0.0-1.0,
      "source_turn_ids": ["turn_id", ...]
    }
  ],
  "personality_traits": [
    {
      "content": "string — an inferred personality trait or communication style preference",
      "confidence": 0.0-1.0
    }
  ],
  "edges": [
    {
      "source_id": "entity_id",
      "target_id": "entity_id",
      "relation": "string",
      "weight": 1.0
    }
  ],
  "duplicate_fact_ids": ["existing_fact_entity_id", ...],
  "duplicate_trait_ids": ["existing_personality_trait_entity_id", ...]
}

Rules:
- Only include genuinely new facts in the facts array. If a fact is already captured by an existing fact entity (listed below), include its ID in duplicate_fact_ids instead.
- Keep the summary concise (2-5 sentences).
- Only extract edges when there is a clear relationship between two named entity IDs.
- NEVER store email addresses, phone numbers, or URLs as facts — not the user's own, and especially not anyone else's.
- NEVER infer or record identity links for third parties (e.g. "Person X's email is Y", "Contact Z works at company W"). You may note that the user collaborates with or knows someone by first name, but do not attach contact details or account information to them.
- Facts about the user's own life, preferences, habits, schedule, interests, and communication style are fine.
- personality_traits capture inferred traits: communication style, working patterns, preferences, personality characteristics. Only include genuinely new traits. If a trait is already captured by an existing personality_trait entity (listed below), include its ID in duplicate_trait_ids instead.
"""


async def trim_conversation_free(client, space_id: str) -> dict:
    """Archive old turns for free-tier users without any LLM summarization.

    Keeps the 5 most recent turns; archives the rest silently.
    No summary, no facts, no edges — just stops the context from growing unbounded.

    Returns {"trimmed": False} if below threshold.
    Returns {"trimmed": True, "turns_archived": N} otherwise.
    """
    turns_result = await (
        client.table("entities")
        .select("id")
        .eq("space_id", space_id)
        .eq("type", "conversation_turn")
        .eq("archived", False)
        .order("created_at", desc=False)
        .execute()
    )
    all_turns = turns_result.data or []

    if len(all_turns) <= cfg.COMPACTION_TURN_THRESHOLD:
        return {"trimmed": False}

    archival_turns = all_turns[:-5]
    ids_to_archive = [t["id"] for t in archival_turns]

    await (
        client.table("entities")
        .update({"archived": True})
        .in_("id", ids_to_archive)
        .execute()
    )

    logger.info(
        "free_tier_trim",
        extra={"space_id": space_id, "turns_archived": len(ids_to_archive)},
    )

    return {"trimmed": True, "turns_archived": len(ids_to_archive)}


async def compact_conversation(
    client, anthropic_client, space_id: str, user_id: str
) -> dict:
    """Compact old conversation turns into a summary, facts, and edges.

    1. Fetch all non-archived turns for the space
    2. Early return if count <= COMPACTION_TURN_THRESHOLD
    3. Archive all but the 5 most recent turns
    4. Query existing facts for deduplication prompt
    5. Call Opus with JSON mode to extract summary + facts + edges
    6. Persist: conversation_summary entity, fact entities, edge entities
    7. Archive old turns
    8. Return stats

    Returns {"compacted": False} if threshold not met.
    Returns {"compacted": True, "summary_id", "fact_count", "edge_count", "turns_archived"} otherwise.
    """
    # Step 1: Fetch all turns
    turns_result = await (
        client.table("entities")
        .select("id, state, created_at")
        .eq("space_id", space_id)
        .eq("type", "conversation_turn")
        .eq("archived", False)
        .order("created_at", desc=False)
        .execute()
    )
    all_turns = turns_result.data or []

    # Step 2: Early return if below threshold
    if len(all_turns) <= cfg.COMPACTION_TURN_THRESHOLD:
        return {"compacted": False}

    # Keep 5 most recent; archive the rest
    archival_turns = all_turns[:-5]

    # Step 3: Fetch existing facts and traits for deduplication
    facts_result, traits_result = await asyncio.gather(
        client.table("entities")
        .select("id, state")
        .eq("space_id", space_id)
        .eq("type", "fact")
        .eq("archived", False)
        .execute(),
        client.table("entities")
        .select("id, state")
        .eq("space_id", space_id)
        .eq("type", "personality_trait")
        .eq("archived", False)
        .execute(),
    )
    existing_facts = facts_result.data or []
    existing_traits = traits_result.data or []

    # Step 4: Build prompt and call Opus
    formatted_turns = [
        {
            "role": t["state"].get("role", "unknown"),
            "content": t["state"].get("content", ""),
        }
        for t in archival_turns
    ]

    existing_facts_text = "\n".join(
        f"- [{f['id']}] {f.get('state', {}).get('content', '')}"
        for f in existing_facts
    ) or "(none)"

    existing_traits_text = "\n".join(
        f"- [{t['id']}] {t.get('state', {}).get('content', '')}"
        for t in existing_traits
    ) or "(none)"

    user_prompt = (
        f"Existing facts in this space:\n"
        f"<existing_facts>\n{existing_facts_text}\n</existing_facts>\n\n"
        f"Existing personality traits in this space:\n"
        f"<existing_traits>\n{existing_traits_text}\n</existing_traits>\n\n"
        f"Conversation to compact:\n"
        f"<turns>\n{json.dumps(formatted_turns, indent=2)}\n</turns>\n\n"
        f"Return JSON with keys: summary, facts, personality_traits, edges, duplicate_fact_ids, duplicate_trait_ids."
    )

    response = await anthropic_client.messages.create(
        model=cfg.COMPACTION_MODEL,
        system=_COMPACTION_SYSTEM,
        messages=[{"role": "user", "content": user_prompt}],
        max_tokens=4096,
    )

    # Record compaction event (fire-and-forget)
    if hasattr(response, "usage"):
        u = response.usage
        from agent.usage import record_usage
        from agent.loop import _bg
        _bg(
            record_usage(client, space_id, user_id, "compaction", {
                "input_tokens": getattr(u, "input_tokens", 0),
                "output_tokens": getattr(u, "output_tokens", 0),
                "turns_compacted": len(archival_turns),
            })
        )

    if not response.content or not hasattr(response.content[0], "text"):
        logger.error("compaction_unexpected_response", extra={"space_id": space_id})
        return {"compacted": False, "error": "unexpected_response"}

    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = re.sub(r'^```\w*\n?', '', raw)
        raw = re.sub(r'\n?```$', '', raw)
        raw = raw.strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.error(
            "compaction_json_parse_error",
            extra={"space_id": space_id, "raw": raw[:200]},
        )
        return {"compacted": False, "error": "json_parse_error"}

    # Step 5: Create conversation_summary entity
    summary_text = parsed.get("summary", "")
    summary_row = {
        "space_id": space_id,
        "user_id": user_id,
        "type": "conversation_summary",
        "presentation": "hidden",
        "state": {"summary": summary_text},
        "summary": f"Conversation summary ({len(archival_turns)} turns)",
        "created_by": "agent",
    }
    summary_result = await client.table("entities").insert(summary_row).execute()
    summary_id = summary_result.data[0]["id"] if summary_result.data else None

    # Steps 6-8: Create fact, personality_trait, and edge entities in parallel.
    # Inner helpers are closures capturing space_id, user_id, client.

    async def _insert_fact(fact: dict) -> bool:
        content = fact.get("content", "").strip()
        if not content:
            return False
        if _contains_pii(content):
            logger.warning("fact_pii_blocked", extra={"content": content[:80]})
            return False
        row = {
            "space_id": space_id, "user_id": user_id, "type": "fact",
            "presentation": "hidden",
            "state": {"content": content, "confidence": fact.get("confidence", 1.0)},
            "summary": content[:100], "created_by": "agent",
        }
        await client.table("entities").insert(row).execute()
        return True

    async def _insert_trait(trait: dict) -> bool:
        content = trait.get("content", "").strip()
        if not content:
            return False
        row = {
            "space_id": space_id, "user_id": user_id, "type": "personality_trait",
            "presentation": "hidden",
            "state": {"content": content, "confidence": trait.get("confidence", 1.0)},
            "summary": content[:100], "created_by": "agent",
        }
        await client.table("entities").insert(row).execute()
        return True

    async def _insert_edge(edge: dict) -> bool:
        source_id = edge.get("source_id", "")
        target_id = edge.get("target_id", "")
        if not source_id or not target_id:
            return False
        row = {
            "space_id": space_id, "user_id": user_id, "type": "edge",
            "presentation": "hidden",
            "state": {
                "source_id": source_id, "target_id": target_id,
                "relation": edge.get("relation", ""), "weight": edge.get("weight", 1.0),
            },
            "summary": f"{source_id} → {target_id} ({edge.get('relation', '')})",
            "created_by": "agent",
        }
        await client.table("entities").insert(row).execute()
        return True

    fact_results, trait_results, edge_results = await asyncio.gather(
        asyncio.gather(*[_insert_fact(f) for f in parsed.get("facts", [])]),
        asyncio.gather(*[_insert_trait(t) for t in parsed.get("personality_traits", [])]),
        asyncio.gather(*[_insert_edge(e) for e in parsed.get("edges", [])]),
    )
    facts_created = sum(1 for r in fact_results if r)
    traits_created = sum(1 for r in trait_results if r)
    edges_created = sum(1 for r in edge_results if r)

    # Step 9: Archive old turns in a single batch update
    ids_to_archive = [t["id"] for t in archival_turns]
    await (
        client.table("entities")
        .update({"archived": True})
        .in_("id", ids_to_archive)
        .execute()
    )
    turns_archived = len(ids_to_archive)

    logger.info(
        "compaction_complete",
        extra={
            "space_id": space_id,
            "summary_id": summary_id,
            "facts_created": facts_created,
            "traits_created": traits_created,
            "edges_created": edges_created,
            "turns_archived": turns_archived,
            "duplicate_fact_ids": parsed.get("duplicate_fact_ids", []),
            "duplicate_trait_ids": parsed.get("duplicate_trait_ids", []),
        },
    )

    return {
        "compacted": True,
        "summary_id": summary_id,
        "fact_count": facts_created,
        "trait_count": traits_created,
        "edge_count": edges_created,
        "turns_archived": turns_archived,
    }
