"""Behavioral engine reconstruction tests with synthetic Responses frames."""

import json
import unittest
from copy import deepcopy
from unittest.mock import patch

from journeymap_memoryreplica.dynamic import (
    advance_case,
    audit_case,
    frame_data,
    parse_frame,
    reconstruct_prefix,
)
from journeymap_memoryreplica.paired import encode, move
from journeymap_memoryreplica.responses import Frame
from journeymap_memoryreplica.responses_probe import mock_frame


def frames(*texts: str) -> tuple[Frame, ...]:
    return tuple(mock_frame(text, index=i) for i, text in enumerate(texts))


RECON = '{"facts":[]}'
WAIT = encode({"action_type": "WAIT", "payload": {"duration": 1}})


class DynamicTests(unittest.TestCase):
    def test_isolated_prefix_boundary_matches_direct_engine_reconstruction(self) -> None:
        saved = frames(RECON, move("A-to-D"))
        value = {
            "memory_policy": "recent",
            "frames": [frame_data(f) for f in saved],
            "case_id": "paired-1",
            "max_decisions": 6,
            "max_ticks": 24,
        }
        self.assertEqual(
            advance_case(value), reconstruct_prefix("recent", saved, case_id="paired-1")
        )
        with self.assertRaises(ValueError):
            advance_case({**value, "authorized": True})

    def test_short_and_long_paths_all_memory_conditions(self) -> None:
        for policy in ("none", "recent", "full"):
            for path, ticks in ((("A-to-S", "S-to-B"), 4), (("A-to-D", "D-to-B"), 8)):
                result = reconstruct_prefix(policy, frames(RECON, *(move(r) for r in path)))
                self.assertEqual((result["stop"], result["final"]["position"]), ("ARRIVED", "B"))
                self.assertEqual(result["final"]["elapsed_ticks"], ticks)
                self.assertIsNone(result["next_wire"])

    def test_request_follows_actual_engine_position(self) -> None:
        left = reconstruct_prefix("recent", frames(RECON, move("A-to-S")))
        right = reconstruct_prefix("recent", frames(RECON, move("A-to-D")))
        self.assertEqual(left["stop"], "NEED_RESPONSE")
        self.assertNotEqual(left["next_wire"]["body"]["input"], right["next_wire"]["body"]["input"])
        self.assertEqual(left["final"]["position"], "S")
        self.assertEqual(right["final"]["position"], "D")

    def test_first_tasks_share_context_without_sharing_reconstruction(self) -> None:
        empty = reconstruct_prefix("full", ())
        after = reconstruct_prefix("full", frames(RECON))
        malformed = reconstruct_prefix("full", frames("{broken"))
        recon_input = json.loads(empty["next_wire"]["body"]["input"])
        action_input = json.loads(after["next_wire"]["body"]["input"])
        self.assertEqual(recon_input["context"], action_input["context"])
        self.assertEqual(after["next_wire"], malformed["next_wire"])
        self.assertEqual(after["final"], empty["final"])

    def test_wait_changes_time_and_following_request(self) -> None:
        result = reconstruct_prefix("full", frames(RECON, WAIT, move("A-to-S"), move("S-to-B")))
        self.assertEqual(result["stop"], "ARRIVED")
        self.assertEqual(result["final"]["elapsed_ticks"], 5)
        self.assertEqual(len(result["action_steps"]), 3)

    def test_decision_limit_has_no_extra_request(self) -> None:
        result = reconstruct_prefix("none", frames(RECON, *([WAIT] * 6)))
        self.assertEqual(result["stop"], "DECISION_LIMIT")
        self.assertEqual(len(result["packets"]), 7)
        self.assertIsNone(result["next_wire"])

    def test_tick_overrun_not_submitted_and_exact_limit_stops(self) -> None:
        result = reconstruct_prefix("full", frames(RECON, move("A-to-S")), max_ticks=1)
        self.assertEqual(result["stop"], "TIME_BUDGET_NOT_SUBMITTED")
        self.assertIsNone(result["action_steps"][0]["engine"])
        self.assertEqual(result["final"]["elapsed_ticks"], 0)
        result = reconstruct_prefix("full", frames(RECON, WAIT), max_ticks=1)
        self.assertEqual(result["stop"], "TIME_LIMIT")
        self.assertIsNone(result["next_wire"])

    def test_malformed_and_incomplete_actions_not_submitted(self) -> None:
        for variant in ("incomplete", "refusal", "partial_frame", "missing_usage", "bad_json"):
            result = reconstruct_prefix("full", (mock_frame(RECON), mock_frame("x", variant, 1)))
            self.assertEqual(result["final"]["position"], "A")
            self.assertEqual(result["final"]["elapsed_ticks"], 0)
            self.assertTrue(all(s["engine"] is None for s in result["action_steps"]))
            self.assertIsNone(result["next_wire"])

    def test_incomplete_reconstruction_allows_independent_action(self) -> None:
        result = reconstruct_prefix("full", (mock_frame(RECON, "incomplete"),))
        self.assertEqual(result["reconstruction"]["status"], "INCOMPLETE")
        self.assertEqual(result["stop"], "NEED_RESPONSE")
        self.assertEqual(result["next_wire"]["kind"], "action")

    def test_uncertain_reconstruction_stops(self) -> None:
        result = reconstruct_prefix("full", (mock_frame(RECON, "partial_frame"),))
        self.assertEqual(result["stop"], "RECONSTRUCTION_OPERATIONAL_STOP")
        self.assertIsNone(result["next_wire"])

    def test_duplicate_response_id_blocks_action(self) -> None:
        result = reconstruct_prefix("full", (mock_frame(RECON), mock_frame(move("A-to-S"))))
        self.assertEqual(result["stop"], "ACTION_DUPLICATE_RESPONSE_ID")
        self.assertEqual(result["final"]["elapsed_ticks"], 0)

    def test_extra_frames_after_arrival_rejected(self) -> None:
        with self.assertRaises(ValueError):
            reconstruct_prefix("full", frames(RECON, move("A-to-S"), move("S-to-B"), WAIT))

    def test_engine_rejection_is_recorded_not_converted_to_success(self) -> None:
        result = reconstruct_prefix("full", frames(RECON, move("unknown-route")))
        self.assertEqual(result["final"]["position"], "A")
        self.assertIsNotNone(result["action_steps"][0]["engine"]["receipt"])
        self.assertNotEqual(result["stop"], "ARRIVED")

    def test_frame_roundtrip_including_partial_invalid_bytes(self) -> None:
        frame = Frame(b"\xff{", complete=False, elapsed_ms=123)
        self.assertEqual(parse_frame(frame_data(frame)), frame)
        for value in (
            {},
            {**frame_data(frame), "body_base64": "!!!"},
            {**frame_data(frame), "elapsed_ms": True},
        ):
            with self.assertRaises(ValueError):
                parse_frame(value)

    def test_replay_and_prefix_replay_without_network(self) -> None:
        data = frames(RECON, move("A-to-S"), move("S-to-B"))
        with (
            patch("socket.socket", side_effect=AssertionError("network forbidden")),
            patch("os.getenv", side_effect=AssertionError("key lookup forbidden")),
        ):
            for count in range(4):
                report = reconstruct_prefix("full", data[:count])
                result = audit_case(report)
                self.assertTrue(result["data_only_equal"])
                self.assertFalse(result["provider_recalled"])
                self.assertFalse(result["paid_journal_verified"])

    def test_replay_detects_derived_data_tampering(self) -> None:
        report = reconstruct_prefix("full", frames(RECON, move("A-to-S"), move("S-to-B")))
        variants = []
        r = deepcopy(report)
        r["final"]["elapsed_ticks"] += 1
        variants.append(r)
        r = deepcopy(report)
        r["packets"][1]["wire"]["body"]["input"] = "changed"
        variants.append(r)
        r = deepcopy(report)
        r["action_steps"][0]["engine"] = None
        variants.append(r)
        r = deepcopy(report)
        r["memory_policy"] = "none"
        variants.append(r)
        for changed in variants:
            with self.assertRaises(ValueError):
                audit_case(changed)

    def test_bad_limits_and_mutable_frame_container_rejected(self) -> None:
        for options in ({"max_decisions": True}, {"max_decisions": 7}, {"max_ticks": 25}):
            with self.assertRaises(ValueError):
                reconstruct_prefix("full", (), **options)
        with self.assertRaises(ValueError):
            reconstruct_prefix("unknown", ())


if __name__ == "__main__":
    unittest.main()
