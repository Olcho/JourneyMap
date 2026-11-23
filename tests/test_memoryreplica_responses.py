"""Offline Responses boundary checks; synthetic actions are not model evidence."""

import json
import unittest
from copy import deepcopy
from decimal import Decimal
from unittest.mock import patch

from journeymap_memoryreplica.paired import encode, move, request
from journeymap_memoryreplica.responses import (
    MAX_OUTPUT_TOKENS,
    MODEL,
    TIMEOUT_MS,
    Frame,
    InertTransport,
    build_wire,
    decode_frame,
    exchange,
    usage_record,
)
from journeymap_memoryreplica.responses_probe import draft_plan, mock_frame, run_mock_pair


class ResponsesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.request = request({}, "action", 6, 37, "B")
        self.document = json.loads(mock_frame(move("A-to-S")).body)

    def test_wire_preserves_input_without_conversation(self) -> None:
        wire = build_wire(self.request)
        body = wire["body"]
        self.assertEqual(body["input"], self.request)
        self.assertEqual(body["model"], MODEL)
        self.assertEqual(body["reasoning"], {"effort": "medium"})
        self.assertEqual(body["max_output_tokens"], MAX_OUTPUT_TOKENS)
        for forbidden in ("previous_response_id", "conversation", "tools"):
            self.assertNotIn(forbidden, body)
        self.assertFalse(body["store"])
        self.assertFalse(body["stream"])

    def test_both_schemas_have_required_object_roots(self) -> None:
        for kind in ("action", "reconstruction"):
            schema = build_wire(request({}, kind, 6, 37, "B"))["body"]["text"]["format"]["schema"]
            self.assertEqual(schema["type"], "object")
            self.assertFalse(schema["additionalProperties"])
            self.assertEqual(set(schema["properties"]), set(schema["required"]))

    def test_usage_does_not_double_count_reasoning(self) -> None:
        record = usage_record(self.document["usage"])
        self.assertEqual(Decimal(record["rate_converted_usd"]), Decimal("0.001065"))
        self.assertEqual(record["reasoning_tokens"], 80)

    def test_missing_cache_is_explicit_upper_estimate(self) -> None:
        del self.document["usage"]["input_tokens_details"]
        record = usage_record(self.document["usage"])
        self.assertIsNone(record["cached_input_tokens"])
        self.assertEqual(record["cost_basis"], "uncached_upper_estimate")
        self.assertEqual(Decimal(record["rate_converted_usd"]), Decimal("0.0012"))

    def test_invalid_usage_counts_rejected(self) -> None:
        for field, value in (("input_tokens", True), ("output_tokens", -1), ("total_tokens", 0)):
            with self.subTest(field=field):
                usage = deepcopy(self.document["usage"])
                usage[field] = value
                with self.assertRaises(ValueError):
                    usage_record(usage)
        for detail, field, value in (
            ("input_tokens_details", "cached_tokens", 1001),
            ("output_tokens_details", "reasoning_tokens", 101),
        ):
            usage = deepcopy(self.document["usage"])
            usage[detail][field] = value
            with self.assertRaises(ValueError):
                usage_record(usage)

    def test_terminal_model_responses_keep_usage_without_text(self) -> None:
        for variant, status in (("incomplete", "INCOMPLETE"), ("refusal", "REFUSAL")):
            outcome = decode_frame(mock_frame("ignored", variant))
            self.assertEqual(outcome["status"], status)
            self.assertIsNone(outcome["text"])
            self.assertIsNotNone(outcome["usage"])

    def test_partial_valid_json_is_uncertain_not_free(self) -> None:
        outcome = decode_frame(mock_frame("ignored", "partial_frame"))
        self.assertEqual(outcome["status"], "TRANSPORT_UNCERTAIN")
        self.assertIsNone(outcome["usage"])
        self.assertIsNone(outcome["text"])

    def test_late_full_response_keeps_usage(self) -> None:
        frame = Frame(mock_frame("ignored").body, elapsed_ms=TIMEOUT_MS + 1)
        outcome = decode_frame(frame)
        self.assertEqual(outcome["status"], "LATE_COMPLETE")
        self.assertIsNotNone(outcome["usage"])
        self.assertIsNone(outcome["text"])

    def test_identity_and_tier_mismatch_not_delivered(self) -> None:
        for field, value in (("model", "other"), ("service_tier", "flex"), ("id", "")):
            document = deepcopy(self.document)
            document[field] = value
            outcome = decode_frame(Frame(encode(document).encode()))
            self.assertEqual(outcome["status"], "IDENTITY_OR_TIER_MISMATCH")
            self.assertIsNone(outcome["text"])
            self.assertIsNone(outcome["usage"])

    def test_invalid_json_duplicate_and_depth_rejected(self) -> None:
        for raw in (b'{"id":1,"id":2}', b"\xff", b"[" * 2000 + b"]" * 2000):
            self.assertEqual(decode_frame(Frame(raw))["status"], "INVALID_RESPONSE")

    def test_tool_or_multiple_messages_rejected(self) -> None:
        self.document["output"].append({"type": "function_call"})
        self.assertEqual(
            decode_frame(Frame(encode(self.document).encode()))["status"], "UNEXPECTED_OUTPUT_ITEM"
        )
        self.document["output"][-1] = deepcopy(self.document["output"][0])
        self.assertEqual(
            decode_frame(Frame(encode(self.document).encode()))["status"], "INVALID_RESPONSE"
        )

    def test_reasoning_item_is_not_engine_text(self) -> None:
        self.document["output"].insert(0, {"type": "reasoning", "summary": []})
        outcome = decode_frame(Frame(encode(self.document).encode()))
        self.assertEqual(outcome["status"], "TEXT_READY")
        self.assertEqual(outcome["text"], move("A-to-S"))

    def test_one_send_no_retry(self) -> None:
        transport = InertTransport(mock_frame("ignored"))
        exchange(self.request, transport)
        with self.assertRaises(ValueError):
            exchange(self.request, transport)
        self.assertEqual(transport.calls, 1)

    def test_http_error_and_missing_usage_not_ready(self) -> None:
        self.assertEqual(
            decode_frame(Frame(mock_frame("x").body, http_status=429))["status"], "HTTP_ERROR"
        )
        self.assertEqual(decode_frame(mock_frame("x", "missing_usage"))["status"], "USAGE_INVALID")

    def test_over_limit_usage_not_ready(self) -> None:
        self.document["usage"].update(output_tokens=8193, total_tokens=9193)
        self.assertEqual(
            decode_frame(Frame(encode(self.document).encode()))["status"], "USAGE_LIMIT_VIOLATION"
        )

    def test_three_mock_pairs_and_identical_initial_contexts(self) -> None:
        with (
            patch("socket.create_connection", side_effect=AssertionError("no network")),
            patch("os.getenv", side_effect=AssertionError("no key lookup")),
        ):
            for policy in ("none", "recent", "full"):
                result = run_mock_pair(policy)
                self.assertEqual(
                    (result["stop"], result["final_position"], result["elapsed_ticks"]),
                    ("ARRIVED", "B", 4),
                )
                packets = result["packets"]
                contexts = [json.loads(p["wire"]["body"]["input"])["context"] for p in packets[:2]]
                self.assertEqual(contexts[0], contexts[1])
                self.assertEqual(result["external_api_calls"], 0)

    def test_failed_first_action_never_changes_engine(self) -> None:
        for variant in ("incomplete", "refusal", "partial_frame", "missing_usage", "bad_json"):
            with self.subTest(variant=variant):
                result = run_mock_pair("full", first_action_variant=variant)
                self.assertEqual(result["engine_steps"], [])
                self.assertEqual((result["final_position"], result["elapsed_ticks"]), ("A", 0))

    def test_reconstruction_output_not_shared_with_action(self) -> None:
        reference = run_mock_pair("full")
        for variant in ("bad_json", "incomplete", "refusal"):
            result = run_mock_pair("full", reconstruction_variant=variant)
            self.assertEqual(result["stop"], "ARRIVED")
            self.assertEqual(result["packets"][1]["wire"], reference["packets"][1]["wire"])

    def test_missing_reconstruction_usage_stops_before_action(self) -> None:
        result = run_mock_pair("full", reconstruction_variant="missing_usage")
        self.assertEqual(len(result["packets"]), 1)
        self.assertEqual(result["engine_steps"], [])

    def test_unhashable_response_item_type_is_rejected_without_raising(self) -> None:
        value: object
        for value in ([], {}):
            with self.subTest(value=value):
                document = deepcopy(self.document)
                document["output"][0]["type"] = value
                outcome = decode_frame(Frame(encode(document).encode()))
                self.assertEqual(outcome["status"], "UNEXPECTED_OUTPUT_ITEM")
                self.assertIsNone(outcome["text"])

    def test_unhashable_task_kind_is_rejected_before_transport(self) -> None:
        value: object
        for value in ([], {}):
            with self.subTest(value=value):
                text = encode(
                    {
                        "profile": "memoryreplica-task-offline-1",
                        "task": {"kind": value},
                        "context": {},
                    }
                )
                transport = InertTransport(mock_frame("ignored"))
                with self.assertRaises(ValueError):
                    exchange(text, transport)
                self.assertEqual(transport.calls, 0)

    def test_plan_is_balanced_and_not_executable(self) -> None:
        plan = draft_plan()
        self.assertFalse(plan["authorized"])
        self.assertEqual(plan["state"], "DRAFT_NOT_EXECUTABLE")
        self.assertEqual(plan["request_cap"], 63)
        self.assertEqual(plan["retries"], 0)
        self.assertEqual(Decimal(plan["batch_conservative_rate_ceiling_usd"]), Decimal("21.222432"))
        self.assertEqual(
            [
                sum(c["memory_policy"] == p for c in plan["cases"])
                for p in ("none", "recent", "full")
            ],
            [3, 3, 3],
        )


if __name__ == "__main__":
    unittest.main()
