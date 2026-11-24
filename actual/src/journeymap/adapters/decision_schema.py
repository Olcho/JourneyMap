"""OpenAI strict subset for candidate intent, never authority fields.

REQUEST alone encodes its arbitrary object as JSON text. The local decoder
restores it losslessly and applies the unchanged M7 payload validator.
"""

from journeymap.core.canonical import JsonObject, JsonValue

DECISION_SCHEMA_VERSION = "decision-candidate-2"


def decision_schema() -> JsonObject:
    string: JsonObject = {"type": "string", "minLength": 1}
    integer: JsonObject = {"type": "integer", "minimum": 1}

    def object_schema(properties: JsonObject) -> JsonObject:
        return {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        }

    payloads: list[JsonValue] = [
        object_schema({"route_id": string}),
        object_schema({"duration": integer}),
        object_schema({"target_actor_id": string, "subject_ref": string, "predicate": string}),
        object_schema({"target_actor_id": string, "claim_record_id": string}),
        object_schema(
            {"target_actor_id": string, "claim_record_id": string, "reply_to_event_id": string}
        ),
        object_schema(
            {
                "target_actor_id": string,
                "request_kind": string,
                "request_payload_json": {
                    "type": "string",
                    "description": "A JSON-encoded object with arbitrary keys/nested JSON values.",
                },
            }
        ),
        object_schema({"item_id": string, "quantity": integer}),
        object_schema({"offer_id": string, "quantity": integer}),
    ]
    # Root anyOf is disallowed. Payload union is constrained by this subset;
    # exact action_type/payload pairing is still enforced locally by M7.
    return object_schema(
        {
            "action_type": {
                "type": "string",
                "enum": ["MOVE", "WAIT", "ASK", "INFORM", "REQUEST", "REST", "CONSUME", "BUY"],
            },
            "payload": {"anyOf": payloads},
        }
    )


def response_format() -> JsonObject:
    return {
        "type": "json_schema",
        "name": "journeymap_decision_v2",
        "strict": True,
        "schema": decision_schema(),
    }
