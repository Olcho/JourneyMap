"""Read saved normal-reference payloads only; no simulation or pytest execution."""
import json
from pathlib import Path
import sys

folder = Path(sys.argv[1]) / "normal-tick16-reference"

def decode(v):
    if not isinstance(v, dict):
        return [decode(x) for x in v] if isinstance(v, list) else v
    if set(v) == {"dict"}:
        return {decode(k): decode(x) for k, x in v["dict"]}
    if len(v) == 1 and next(iter(v)) in {"tuple", "list", "set"}:
        return [decode(x) for x in next(iter(v.values()))]
    if set(v) == {"record", "fields"}:
        return {k: decode(x) for k, x in v["fields"].items()}
    if set(v) == {"enum", "value"}:
        return v["value"]
    return {k: decode(x) for k, x in v.items()}

samples = {n: json.loads((folder / f"tick-{n}-payload.json").read_text(encoding="utf-8")) for n in (15, 16, 17)}
result = {}
for left, right in ((15, 16), (16, 17)):
    a, b = samples[left], samples[right]
    ka, kb = a["objects"]["kernel"], b["objects"]["kernel"]
    sa, sb = a["objects"]["session"], b["objects"]["session"]
    wa, wb = decode(ka["_state"]), decode(kb["_state"])
    ea, eb = decode(ka["_events"]), decode(kb["_events"])
    result[f"{left}->{right}"] = {
        "changed_world_modules": [k for k in wa if wa[k] != wb[k]],
        "changed_kernel_fields": [k for k in ka if ka[k] != kb[k]],
        "changed_driver_fields": [k for k in sa if sa[k] != sb[k]],
        "cursor": [decode(sa["_cursor"]), decode(sb["_cursor"])],
        "new_events": [{"event_type": e["event_type"], "payload": e["payload"]} for e in eb[len(ea):]],
        "source_compatibility_same": a["compatibility"] == b["compatibility"],
        "rng_same": a["rng"] == b["rng"],
        "world_states": {str(left): wa, str(right): wb},
    }
with (folder / "normal-transition-analysis.json").open("x", encoding="utf-8") as stream:
    json.dump(result, stream, indent=2, ensure_ascii=False)
for k, v in result.items():
    print(k, json.dumps({x: y for x, y in v.items() if x not in {"world_states", "new_events"}}))
    print("EVENT_TYPES", [e["event_type"] for e in v["new_events"]])
