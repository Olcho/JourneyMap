import copy
import json
import os
import socket
import unittest
from collections.abc import Iterator
from typing import Any, ClassVar

import pytest

from journeymap_memoryreplica.paired import OfflineReplies, run_pair
from journeymap_memoryreplica.task import collect, shortest_path, supported_edges


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


class TaskTests(unittest.TestCase):
    data: ClassVar[dict[str, Any]]
    rows: ClassVar[dict[Any, Any]]

    @classmethod
    def setUpClass(cls) -> None:
        cls.data = collect()
        cls.rows = {r["memory_policy"]: r for r in cls.data["inputs"]["rows"]}

    def test_shared_experience_and_world(self) -> None:
        self.assertEqual(len({r["full_experience_digest"] for r in self.rows.values()}), 1)
        self.assertEqual(len({r["movement_digest"] for r in self.rows.values()}), 1)

    def test_old_shortcut_changes_supported_route_cost(self) -> None:
        self.assertEqual(self.rows["full"]["initial_evidence_plan_to_B"]["cost"], 4)
        self.assertEqual(self.rows["recent"]["initial_evidence_plan_to_B"]["cost"], 8)
        self.assertEqual(
            self.rows["full"]["initial_evidence_plan_to_B"]["routes"], ["A-to-S", "S-to-B"]
        )
        self.assertTrue(
            all(r["research_only_world_optimum_to_B"]["cost"] == 4 for r in self.rows.values())
        )

    def test_none_has_no_complete_initial_path_but_can_rediscover(self) -> None:
        self.assertIsNone(self.rows["none"]["initial_evidence_plan_to_B"]["cost"])
        witness = next(
            w
            for w in self.data["witnesses"]
            if w["memory_policy"] == "none" and w["scripted_path_label"] == "short_path"
        )
        self.assertTrue(witness["action"]["goal_reached"])
        at_s = json.loads(witness["action"]["steps"][1]["request_text"])["context"]
        self.assertEqual(at_s["observation"]["position"], "S")
        self.assertEqual(at_s["event_memory"], [])
        self.assertIn("S-to-B", {r["route_id"] for r in at_s["observation"]["local_routes"]})

    def test_both_witness_paths_execute_in_all_policies(self) -> None:
        self.assertEqual(len(self.data["witnesses"]), 6)
        for pair in self.data["witnesses"]:
            self.assertEqual(pair["action"]["final_position"], "B")
            self.assertEqual(
                pair["action"]["elapsed_ticks"],
                4 if pair["scripted_path_label"] == "short_path" else 8,
            )
            self.assertEqual(pair["action"]["stop"], "ARRIVED")

    def test_goal_is_common_and_diagnostic_path_is_not_injected(self) -> None:
        for pair in self.data["witnesses"]:
            recon = json.loads(pair["reconstruction"]["request_text"])
            first = json.loads(pair["action"]["steps"][0]["request_text"])
            self.assertEqual(recon["task"]["goal"], "B")
            self.assertEqual(first["task"]["goal"], "B")
            self.assertEqual(recon["context"], first["context"])
            self.assertNotIn(
                "research_only_world_optimum", pair["action"]["steps"][0]["request_text"]
            )

    def test_reinstating_only_forgotten_observation_restores_shortcut(self) -> None:
        # Diagnostic intervention on the visible input, not a new experimental arm.
        context = copy.deepcopy(self.rows["recent"]["context"])
        context["event_memory"].insert(
            0, copy.deepcopy(self.rows["full"]["context"]["event_memory"][0])
        )
        result = shortest_path(supported_edges(context), "A", "B")
        self.assertEqual(result["cost"], 4)

    def test_no_reverse_edges_inferred_from_route_names(self) -> None:
        edges = [
            {"route_id": "A-to-B", "origin": "A", "destination": "B", "cost": 2, "passable": True}
        ]
        self.assertIsNone(shortest_path(edges, "B", "A")["cost"])

    def test_closed_shortcut_is_not_used(self) -> None:
        edges = copy.deepcopy(self.rows["full"]["supported_edges"])
        for edge in edges:
            if edge["route_id"] == "S-to-B":
                edge["passable"] = False
        self.assertEqual(shortest_path(edges, "A", "B")["cost"], 8)

    def test_unsupported_goal_rejected_before_provider_calls(self) -> None:
        recon, actions = OfflineReplies(("{}",)), OfflineReplies(("{}",))
        with self.assertRaises(ValueError):
            run_pair("unchanged", "none", recon, actions, goal="Z")
        self.assertEqual(recon.requests, [])
        self.assertEqual(actions.requests, [])


if __name__ == "__main__":
    unittest.main()
