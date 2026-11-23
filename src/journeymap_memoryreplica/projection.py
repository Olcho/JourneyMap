"""Offline MemoryReplica input and evidence-recall prototype (not a live runner).

Accepts the already validated E04 semantic input. It never reads World Truth.
Historical E04 inputs and profiles are not modified.
"""

from copy import deepcopy
from typing import Any, cast


def project_context(semantic: dict[str, Any], policy: str, k: int = 3) -> dict[str, Any]:
    """Retain local observations and newly acquired updates, then select memory.

    Authored initial maps are excluded from ALL conditions in this exploratory
    local-experience variant. Updates are captured before memory selection so
    forgotten knowledge cannot reappear through the cumulative knowledge list.
    This is a new task variant, not an equivalent rendering of historical E04.
    """
    if policy not in {"none", "recent", "full"}:
        raise ValueError("unsupported memory policy")
    if type(k) is not int or k < 1:
        raise ValueError("k must be a positive integer")
    previous_time = -1

    def observation(raw: dict[str, Any]) -> dict[str, Any]:
        nonlocal previous_time
        now = raw["time"]
        if now < previous_time:
            raise ValueError("observations must be chronological")
        updates = [
            deepcopy(record)
            for record in raw["knowledge"]
            if record["claim"]["kind"] != "initial-map"
            and previous_time < record["learned_at"] <= now
        ]
        previous_time = now
        return {
            "time": now,
            "position": raw["position"],
            "local_routes": deepcopy(raw["local_routes"]),
            "received_updates": updates,
        }

    history: list[dict[str, Any]] = []
    for trace in semantic["event_memory"]:
        history.append(
            {
                "observation": observation(trace["observation"]),
                **{
                    key: deepcopy(trace[key])
                    for key in ("action", "receipt", "opened_at", "closed_at")
                },
            }
        )
    current = observation(semantic["observation"])
    selected = [] if policy == "none" else history[-k:] if policy == "recent" else history
    # Policy labels, case names and hidden state do not enter model-facing input.
    return {"observation": current, "event_memory": selected}


def evidence_atoms(context: dict[str, Any]) -> list[dict[str, Any]]:
    """Facts explicitly present in this input, timestamped and source-addressed.

    A last-seen fact is not a claim that the route still has that state now.
    Failed movement receipts are retained in context but are not translated into
    map facts: the visible local observation/update supplies map evidence here.
    """
    atoms = []
    sources = [
        (f"memory/{i}", trace["observation"]) for i, trace in enumerate(context["event_memory"])
    ]
    sources.append(("current", context["observation"]))
    for source, observation in sources:
        for i, route in enumerate(observation["local_routes"]):
            values = {"origin": observation["position"], **route}
            for field in ("origin", "destination", "cost", "passable"):
                atoms.append(
                    {
                        "route_id": route["route_id"],
                        "field": field,
                        "value": values[field],
                        "as_of": observation["time"],
                        "evidence": f"{source}/local_routes/{i}",
                    }
                )
        for i, update in enumerate(observation["received_updates"]):
            claim = update["claim"]
            if claim["kind"] != "route-status":
                raise ValueError("unsupported update kind")
            for route_id in claim["route_ids"]:
                atoms.append(
                    {
                        "route_id": route_id,
                        "field": "passable",
                        "value": claim["passable"],
                        "as_of": update["learned_at"],
                        "evidence": f"{source}/received_updates/{i}",
                    }
                )
    return atoms


def _key(atom: dict[str, Any]) -> tuple[str, str]:
    return cast(str, atom["route_id"]), cast(str, atom["field"])


def latest_facts(context: dict[str, Any]) -> list[dict[str, Any]]:
    """Deterministic reference extraction, never a simulated LLM response."""
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for atom in evidence_atoms(context):
        key = _key(atom)
        if (
            key in latest
            and latest[key]["as_of"] == atom["as_of"]
            and latest[key]["value"] != atom["value"]
        ):
            raise ValueError("conflicting evidence at same time")
        if key not in latest or latest[key]["as_of"] <= atom["as_of"]:
            latest[key] = atom
    return [latest[key] for key in sorted(latest)]


def score_recall(context: dict[str, Any], claims: list[dict[str, Any]]) -> dict[str, Any]:
    """Score latest-available evidence recall, NOT current-world accuracy.

    Claims: {route_id, field, value, as_of, evidence}. Unknown uses three nulls.
    A supported older claim is stale, not fabricated. Unknown is distinct from
    omission or contradiction. The evidence reference must actually exist.
    """
    all_atoms = evidence_atoms(context)
    expected = {_key(atom): atom for atom in latest_facts(context)}
    seen = set()
    counts = dict.fromkeys(
        ("supported_latest", "supported_older", "unknown", "contradicted", "unsupported"), 0
    )
    details = []
    for claim in claims:
        if set(claim) != {"route_id", "field", "value", "as_of", "evidence"}:
            raise ValueError("invalid claim fields")
        if type(claim["route_id"]) is not str or not claim["route_id"]:
            raise ValueError("invalid route id")
        field = claim["field"]
        if field not in {"origin", "destination", "cost", "passable"}:
            raise ValueError("invalid claim field")
        key = _key(claim)
        if key in seen:
            raise ValueError("duplicate route/field claim")
        seen.add(key)
        if claim["value"] is None:
            if claim["as_of"] is not None or claim["evidence"] is not None:
                raise ValueError("unknown must have null time and evidence")
            category = "unknown"
        else:
            required_type = bool if field == "passable" else int if field == "cost" else str
            if type(claim["value"]) is not required_type:
                raise ValueError("invalid value type")
            if field == "cost" and cast(int, claim["value"]) <= 0:
                raise ValueError("cost must be positive")
            if type(claim["as_of"]) is not int or claim["as_of"] < 0:
                raise ValueError("invalid evidence time")
            if type(claim["evidence"]) is not str or not claim["evidence"]:
                raise ValueError("invalid evidence reference")
            matching = [
                a
                for a in all_atoms
                if _key(a) == key
                and a["as_of"] == claim["as_of"]
                and a["evidence"] == claim["evidence"]
            ]
            if not matching:
                category = "unsupported"
            elif not any(a["value"] == claim["value"] for a in matching):
                category = "contradicted"
            elif claim["as_of"] < expected[key]["as_of"]:
                category = "supported_older"
            else:
                category = "supported_latest"
        counts[category] += 1
        details.append({"route_id": key[0], "field": key[1], "category": category})
    missing = sorted(set(expected) - seen)
    return {
        "expected_facts": len(expected),
        "submitted_claims": len(claims),
        **counts,
        "omitted_known": len(missing),
        "omitted_keys": [list(key) for key in missing],
        "details": details,
    }
