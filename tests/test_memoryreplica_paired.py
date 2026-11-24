"""Separation, malformed output and trusted engine integration checks."""

import json
import os
import socket
import unittest
from collections.abc import Iterator
from typing import Any, ClassVar

import pytest

from journeymap_memoryreplica.paired import (
    OfflineReplies,
    collect_pairs,
    encode,
    move,
    reconstruction_record,
    reference_response,
    run_pair,
)


@pytest.fixture(scope="module", autouse=True)
def offline_only() -> Iterator[None]:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("MemoryReplica must not use network or credentials")

    previous = os.environ.get

    def no_key(key: str, default: str | None = None) -> str | None:
        if key == "OPENAI_API_KEY":
            forbidden()
        return previous(key, default)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(socket, "create_connection", forbidden)
        patch.setattr(socket.socket, "connect", forbidden)
        patch.setattr(socket.socket, "connect_ex", forbidden)
        patch.setattr(os.environ, "get", no_key)
        yield


class PairedTests(unittest.TestCase):
    data: ClassVar[dict[str, Any]]
    rows: ClassVar[dict[Any, Any]]

    @classmethod
    def setUpClass(cls) -> None:
        cls.data = collect_pairs()

    def test_same_experience_and_world_across_memory_policies(self) -> None:
        for condition in ("unchanged", "closed-unannounced", "closed-notified"):
            rows = [p for p in self.data["pairs"] if p["condition"] == condition]
            self.assertEqual(len({p["baseline"]["movement_state_digest"] for p in rows}), 1)
            self.assertEqual(len({p["baseline"]["full_experience_digest"] for p in rows}), 1)
            for row in rows:
                recon = json.loads(row["reconstruction"]["request_text"])
                action = json.loads(row["action"]["steps"][0]["request_text"])
                self.assertEqual(recon["context"], action["context"])
                self.assertNotEqual(recon["task"]["kind"], action["task"]["kind"])

    def test_reconstruction_response_cannot_change_action_inputs_or_outcome(self) -> None:
        outputs = (move("A-to-D"), move("D-to-G"))
        good = run_pair(
            "closed-unannounced",
            "full",
            OfflineReplies((reference_response("closed-unannounced", "full"),)),
            OfflineReplies(outputs),
        )
        bad = run_pair(
            "closed-unannounced",
            "full",
            OfflineReplies(("DO_NOT_MOVE_SENTINEL",)),
            OfflineReplies(outputs),
        )
        self.assertEqual(bad["reconstruction"]["status"], "INVALID_OUTPUT")
        self.assertEqual(good["action"], bad["action"])
        self.assertNotIn("DO_NOT_MOVE_SENTINEL", encode(bad["action"]))

    def test_no_research_metadata_or_scores_in_requests(self) -> None:
        for pair in self.data["pairs"]:
            requests = [pair["reconstruction"]["request_text"]] + [
                s["request_text"] for s in pair["action"]["steps"]
            ]
            for text in requests:
                request = json.loads(text)
                self.assertEqual(set(request), {"profile", "task", "context"})
                self.assertEqual(set(request["context"]), {"observation", "event_memory"})
                self.assertNotIn('"condition"', text)
                self.assertNotIn('"movement_state_digest"', text)
                self.assertNotIn('"current_world_comparison"', text)

    def test_memory_policy_is_reapplied_after_action(self) -> None:
        for pair in self.data["pairs"]:
            context = json.loads(pair["action"]["steps"][1]["request_text"])["context"]
            self.assertEqual(context["observation"]["position"], "D")
            self.assertEqual(
                len(context["event_memory"]),
                {"none": 0, "recent": 3, "full": 5}[pair["memory_policy"]],
            )

    def test_offline_engine_actions_reach_goal(self) -> None:
        self.assertEqual(len(self.data["pairs"]), 9)
        for pair in self.data["pairs"]:
            self.assertEqual(pair["reconstruction"]["status"], "VALID")
            self.assertEqual(pair["action"]["stop"], "ARRIVED")
            self.assertEqual(pair["action"]["elapsed_ticks"], 6)
            self.assertEqual(len(pair["action"]["steps"]), 2)
            self.assertTrue(
                all(
                    s["engine"]["receipt"]["status"] == "SUCCEEDED" for s in pair["action"]["steps"]
                )
            )

    def test_malformed_reconstruction_is_recorded_without_repair(self) -> None:
        context = json.loads(self.data["pairs"][0]["reconstruction"]["request_text"])["context"]
        invalid = (
            '{"facts":[],"facts":[]}',
            '{"facts":[null]}',
            '{"facts":[{"field":[]}]}',
            '{"facts":NaN}',
            "[" * 1500 + "0" + "]" * 1500,
            "x" * 65537,
        )
        for text in invalid:
            with self.subTest(text=text[:30]):
                result = reconstruction_record(text, context, {})
                self.assertEqual(result["status"], "INVALID_OUTPUT")
                self.assertEqual(result["raw_response"], text)

    def test_invalid_action_never_reaches_engine(self) -> None:
        pair = run_pair(
            "unchanged",
            "none",
            OfflineReplies(('{"facts":[]}',)),
            OfflineReplies(('{"action_type":"MOVE","payload":{"route_id":false}}',)),
        )
        self.assertEqual(pair["action"]["stop"], "INVALID_ACTION_OUTPUT")
        self.assertEqual(pair["action"]["elapsed_ticks"], 0)
        self.assertIsNone(pair["action"]["steps"][0]["engine"])

    def test_time_limit_checked_before_submission(self) -> None:
        pair = run_pair(
            "unchanged",
            "none",
            OfflineReplies(('{"facts":[]}',)),
            OfflineReplies((encode({"action_type": "WAIT", "payload": {"duration": 25}}),)),
        )
        self.assertEqual(pair["action"]["stop"], "TIME_BUDGET_NOT_SUBMITTED")
        self.assertEqual(pair["action"]["elapsed_ticks"], 0)

    def test_different_current_world_does_not_overwrite_historical_recall_score(self) -> None:
        context: dict[str, Any] = {
            "observation": {
                "time": 13,
                "position": "A",
                "local_routes": [],
                "received_updates": [],
            },
            "event_memory": [
                {
                    "observation": {
                        "time": 5,
                        "position": "C",
                        "received_updates": [],
                        "local_routes": [
                            {"route_id": "C-to-G", "destination": "G", "cost": 2, "passable": True}
                        ],
                    }
                }
            ],
        }
        text = encode(
            {
                "facts": [
                    {
                        "route_id": "C-to-G",
                        "field": "passable",
                        "value": True,
                        "as_of": 5,
                        "evidence": "memory/0/local_routes/0",
                    }
                ]
            }
        )
        result = reconstruction_record(text, context, {"C-to-G": {"passable": False}})
        self.assertEqual(result["recall"]["supported_latest"], 1)
        self.assertEqual(result["recall"]["contradicted"], 0)
        self.assertEqual(result["current_world_comparison"]["different_current_value"], 1)

    def test_shared_provider_is_rejected(self) -> None:
        provider = OfflineReplies(('{"facts":[]}',))
        with self.assertRaises(ValueError):
            run_pair("unchanged", "none", provider, provider)


if __name__ == "__main__":
    unittest.main()
