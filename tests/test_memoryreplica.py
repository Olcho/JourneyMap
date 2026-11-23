"""Focused correctness checks, including real offline E04 engine witnesses."""

import copy
import os
import socket
import unittest
from collections.abc import Iterator
from typing import Any, ClassVar, cast

import pytest

from journeymap_memoryreplica.probe import collect
from journeymap_memoryreplica.projection import (
    evidence_atoms,
    latest_facts,
    project_context,
    score_recall,
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


class PrototypeTests(unittest.TestCase):
    data: ClassVar[dict[str, Any]]
    rows: ClassVar[dict[Any, Any]]

    @classmethod
    def setUpClass(cls) -> None:
        cls.data = collect()
        cls.rows = {(r["condition"], r["index"]): r for r in cls.data["records"]}

    def row(self, condition: str = "closed-unannounced", index: int = 3) -> dict[str, Any]:
        return cast(dict[str, Any], self.rows[condition, index])

    def test_hidden_remote_closure_does_not_change_initial_inputs(self) -> None:
        # Same visible experience, different World Truth: equality must survive.
        stable = self.row("unchanged", 0)
        hidden = self.row("closed-unannounced", 0)
        for policy in ("none", "recent", "full"):
            self.assertEqual(stable["prototype_inputs"][policy], hidden["prototype_inputs"][policy])

    def test_e04_memory_removal_leaves_initial_map(self) -> None:
        self.assertTrue(
            all(r["e04_current_initial_map_routes"] == 18 for r in self.data["records"])
        )

    def test_none_does_not_reintroduce_old_closure_knowledge(self) -> None:
        row = self.row()  # A, after visiting C and D.
        self.assertEqual(row["position"], "A")
        self.assertGreater(row["e04_current_status_records"], 0)
        none = row["prototype_inputs"]["none"]
        self.assertEqual(none["observation"]["received_updates"], [])
        self.assertNotIn("C-to-G", {a["route_id"] for a in latest_facts(none)})
        full = row["prototype_inputs"]["full"]
        facts = [
            a for a in latest_facts(full) if a["route_id"] == "C-to-G" and a["field"] == "passable"
        ]
        self.assertEqual(len(facts), 1)
        self.assertIs(facts[0]["value"], False)

    def test_current_local_closure_remains_visible_without_memory(self) -> None:
        context = self.row(index=1)["prototype_inputs"]["none"]
        facts = [
            a
            for a in latest_facts(context)
            if a["route_id"] == "C-to-G" and a["field"] == "passable"
        ]
        self.assertEqual(len(facts), 1)
        self.assertIs(facts[0]["value"], False)

    def test_notice_is_visible_when_received_but_not_permanent_current_input(self) -> None:
        now = self.row("closed-notified", 0)["prototype_inputs"]["none"]
        self.assertEqual(
            now["observation"]["received_updates"][0]["source_kind"], "OFFICIAL_NOTICE"
        )
        later = self.row("closed-notified", 3)["prototype_inputs"]["none"]
        self.assertEqual(later["observation"]["received_updates"], [])

    def test_recent_three_can_differ_from_full(self) -> None:
        row = self.row(index=4)
        recent = row["prototype_inputs"]["recent"]
        full = row["prototype_inputs"]["full"]
        self.assertEqual(len(recent["event_memory"]), 3)
        self.assertEqual(len(full["event_memory"]), 5)
        self.assertNotIn("S-to-B", {a["route_id"] for a in latest_facts(recent)})
        self.assertIn("S-to-B", {a["route_id"] for a in latest_facts(full)})

    def test_projection_does_not_mutate_source_or_share_mutable_lists(self) -> None:
        raw = self.row()["historical_e04_input"]
        before = copy.deepcopy(raw)
        result = project_context(raw, "full")
        result["observation"]["local_routes"].clear()
        result["event_memory"].clear()
        self.assertEqual(raw, before)

    def test_attempted_closed_route_is_engine_rejection_not_transport_failure(self) -> None:
        receipt = self.row(index=4)["following_scripted_receipt"]
        self.assertEqual(receipt["status"], "REJECTED")
        self.assertEqual(receipt["reason_code"], "ROUTE_IMPASSABLE")
        # Rejected action was captured as a closed experience, retaining no extra time.
        last = self.row(index=5)["prototype_inputs"]["full"]["event_memory"][-1]
        self.assertEqual(last["receipt"]["status"], "REJECTED")
        self.assertEqual(self.row(index=4)["time"], self.row(index=5)["time"])

    def test_scorer_distinguishes_unknown_omitted_false_and_unsupported(self) -> None:
        context = self.row(index=1)["prototype_inputs"]["none"]
        facts = latest_facts(context)
        unknown = {**facts[0], "value": None, "as_of": None, "evidence": None}
        false = next(copy.deepcopy(a) for a in facts if a["field"] == "passable")
        false["value"] = not false["value"]
        invented = {
            "route_id": "invented",
            "field": "cost",
            "value": 2,
            "as_of": 5,
            "evidence": "current/local_routes/0",
        }
        score = score_recall(context, [unknown, false, invented])
        self.assertEqual(score["unknown"], 1)
        self.assertEqual(score["contradicted"], 1)
        self.assertEqual(score["unsupported"], 1)
        self.assertEqual(score["omitted_known"], len(facts) - 2)

    def test_old_valid_evidence_is_not_marked_false(self) -> None:
        context = self.row(index=4)["prototype_inputs"]["full"]
        atoms = evidence_atoms(context)
        old = next(a for a in atoms if a["route_id"] == "C-to-G" and a["field"] == "passable")
        score = score_recall(context, [old])
        self.assertEqual(score["supported_older"], 1)
        self.assertEqual(score["contradicted"], 0)

    def test_duplicate_claim_or_bool_cost_is_rejected(self) -> None:
        context = self.row()["prototype_inputs"]["none"]
        claim = latest_facts(context)[0]
        with self.assertRaises(ValueError):
            score_recall(context, [claim, claim])
        bad = {**claim, "field": "cost", "value": True}
        with self.assertRaises(ValueError):
            score_recall(context, [bad])

    def test_reference_extraction_is_completely_scoreable(self) -> None:
        for row in self.data["records"]:
            for context in row["prototype_inputs"].values():
                facts = latest_facts(context)
                score = score_recall(context, facts)
                self.assertEqual(score["supported_latest"], len(facts))
                self.assertEqual(score["omitted_known"], 0)


if __name__ == "__main__":
    unittest.main()
