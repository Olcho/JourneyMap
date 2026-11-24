"""Checks for the scientific comparison and the actual engine boundary."""

from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(os.environ.get("MR_V1_SOURCE_SRC", ROOT.parent / "execution_integration/research/src"))
PROTOCOL = Path(os.environ.get("MR_V1_PROTOCOL_DIR", ROOT / "protocol" if (ROOT / "protocol").is_dir()
                               else ROOT.parent / "protocol_v1_2026-11-05"))
sys.path[:0] = [str(ROOT), str(SOURCE)]

from memoryreplica_v1.adapter import POLICIES, advance, digest, load_cases, load_protocol
from memoryreplica_v1.analysis import aggregate
from memoryreplica_v1.evaluate import bounded_path, evaluate_reconstruction, parse_facts
from fixtures import answer, perfect_reconstruction, run_fixture


class V1Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.protocol, cls.prompts = load_protocol(PROTOCOL)
        cls.cases = load_cases(PROTOCOL, "development")
        cls.case = cls.cases[0]

    def initial(self, policy="M"):
        return advance(self.case, policy, [], self.protocol, self.prompts)

    def after_map(self, text=None):
        first = self.initial()
        record = answer(first, text or perfect_reconstruction(self.case["contexts"]["M"]))
        return advance(self.case, "M", [record], self.protocol, self.prompts)

    def test_frozen_inputs_match_engine_and_do_not_expose_truth(self):
        episode_ids = set()
        for policy in POLICIES:
            result = self.initial(policy)
            episode_ids.add(result["episode_id"])
            request = json.loads(result["next_wire"]["body"]["input"])
            self.assertEqual(request["context"], self.case["contexts"][policy])
            self.assertNotIn("world_truth", request)
            self.assertNotIn("oracle_path", request)
            self.assertNotIn("policy", request)
            self.assertNotIn("case_id", request)
        self.assertEqual(len(episode_ids), 5)

    def test_wire_matches_existing_actual_canonical_serialization(self):
        from journeymap.core.canonical import canonical_json
        wire = self.initial()["next_wire"]
        self.assertEqual(wire["canonical_utf8"], canonical_json(wire["body"]))

    def test_map_output_never_enters_action_request(self):
        a = self.after_map()
        b = self.after_map('{"facts":[],"CANARY_DO_NOT_COPY":123}')
        self.assertEqual(a["next_wire"], b["next_wire"])
        self.assertEqual(a["next_wire"]["sha256"], self.initial()["baseline"]["first_action_sha256"])
        self.assertNotIn("CANARY_DO_NOT_COPY", b["next_wire"]["canonical_utf8"])

    def test_archive_stays_fixed_and_feedback_accumulates(self):
        report = self.after_map()
        current = self.case["contexts"]["M"]["current"]
        route = next(r for r in current["local_routes"] if r["destination"] != self.case["goal"])
        command = {"action_type": "MOVE", "payload": {"route_id": route["route_id"]}}
        saved = report["saved"] + [answer(report, json.dumps(command))]
        after = advance(self.case, "M", saved, self.protocol, self.prompts)
        context = json.loads(after["next_wire"]["body"]["input"])["context"]
        self.assertEqual(context["archive"], self.case["contexts"]["M"]["archive"])
        self.assertEqual(context["current"]["time"], current["time"] + route["cost"])
        self.assertEqual(context["current"]["position"], route["destination"])
        self.assertEqual(context["task"]["remaining_decisions"], 5)
        self.assertEqual(context["last_receipt"]["status"], "SUCCEEDED")
        self.assertEqual(context["current_episode_history"][0]["observation"], current)

    def test_rejected_moves_consume_six_decisions(self):
        report = self.after_map()
        for _ in range(6):
            saved = report["saved"] + [answer(report, '{"action_type":"MOVE","payload":{"route_id":"missing"}}')]
            report = advance(self.case, "M", saved, self.protocol, self.prompts)
        self.assertEqual(report["stop"], "DECISION_LIMIT")
        self.assertEqual(report["final"]["decisions"], 6)
        self.assertEqual(report["score"], 0)
        self.assertEqual(report["final"]["elapsed_ticks"], 0)

    def test_wait_past_deadline_is_not_executed(self):
        report = self.after_map()
        saved = report["saved"] + [answer(report, '{"action_type":"WAIT","payload":{"duration":25}}')]
        after = advance(self.case, "M", saved, self.protocol, self.prompts)
        self.assertEqual(after["stop"], "TIME_BUDGET_NOT_SUBMITTED")
        self.assertEqual(after["final"]["elapsed_ticks"], 0)
        self.assertEqual(after["score"], 0)

    def test_known_model_failure_is_zero_external_failure_is_missing(self):
        for options, score in (({"refusal": True}, 0), ({"status": "incomplete"}, 0),
                               ({"http": 503}, None), ({"complete": False}, None)):
            report = self.after_map()
            saved = report["saved"] + [answer(report, "{}", **options)]
            after = advance(self.case, "M", saved, self.protocol, self.prompts)
            self.assertEqual(after["score"], score)
            self.assertEqual(after["final"]["elapsed_ticks"], 0)

    def test_invalid_action_is_reported_as_model_failure(self):
        report = self.after_map()
        saved = report["saved"] + [answer(report, '{"action_type":"WORK","payload":{}}')]
        after = advance(self.case, "M", saved, self.protocol, self.prompts)
        self.assertEqual(after["stop"], "INVALID_ACTION_OUTPUT")
        self.assertEqual(after["score"], 0)

    def test_saved_response_is_bound_to_its_request(self):
        result = self.initial()
        saved = answer(result, "{\"facts\":[]}")
        saved["wire_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "different request"):
            advance(self.case, "M", [saved], self.protocol, self.prompts)

    def test_semantics_and_provenance_are_separate(self):
        context = self.case["contexts"]["M"]
        text = perfect_reconstruction(context)
        correct = evaluate_reconstruction(self.case, context, text)
        bad = json.loads(text)
        for fact in bad["facts"]:
            fact["evidence"] = "wrong"
        misquoted = evaluate_reconstruction(self.case, context, json.dumps(bad))
        self.assertEqual(correct["world_edge_f1"], misquoted["world_edge_f1"])
        self.assertEqual(correct["input_conditional_accuracy"], 1)
        self.assertEqual(misquoted["input_conditional_accuracy"], 1)
        self.assertEqual(misquoted["provenance_accuracy"], 0)

    def test_hallucinated_shortcut_is_not_a_valid_world_path(self):
        context = self.case["contexts"]["M"]
        fake = {"origin": self.case["start"], "destination": self.case["goal"], "cost": 1, "passable": True}
        facts = [{"route_id": "invented", "field": k, "value": v, "as_of": 0,
                  "evidence": "archive/0"} for k, v in fake.items()]
        result = evaluate_reconstruction(self.case, context, json.dumps({"facts": facts}))
        self.assertEqual(result["claimed_path"]["cost"], 1)
        self.assertIsNone(result["truth_validated_claimed_path"]["cost"])
        self.assertEqual(result["world_edge_f1"], 0)

    def test_unknown_partial_duplicates_and_bool_cost(self):
        context = self.case["contexts"]["M"]
        good = json.loads(perfect_reconstruction(context))
        duplicate = {"facts": good["facts"] + [good["facts"][0]]}
        with self.assertRaisesRegex(ValueError, "duplicate"):
            parse_facts(json.dumps(duplicate))
        partial = deepcopy(good)
        partial["facts"].pop()
        a = evaluate_reconstruction(self.case, context, json.dumps(partial))
        self.assertEqual(a["input_missing_facts"], 1)
        wrong = deepcopy(good)
        next(f for f in wrong["facts"] if f["field"] == "cost")["value"] = True
        b = evaluate_reconstruction(self.case, context, json.dumps(wrong))
        self.assertEqual(b["input_wrong_facts"], 1)
        self.assertLess(b["world_edge_f1"], evaluate_reconstruction(self.case, context, json.dumps(good))["world_edge_f1"])

    def test_reference_path_respects_the_action_limit(self):
        edges = [{"route_id": f"r{i}", "origin": str(i), "destination": str(i+1),
                  "cost": 1, "passable": True} for i in range(7)]
        edges.append({"route_id": "direct", "origin": "0", "destination": "7", "cost": 8, "passable": True})
        self.assertEqual(bounded_path(edges, "0", "7", 7)["cost"], 7)
        self.assertEqual(bounded_path(edges, "0", "7", 6)["cost"], 8)

    def test_closed_loop_and_replay_are_identical(self):
        report = run_fixture(self.case, "M", self.protocol, self.prompts)
        self.assertIsNotNone(report["score"])
        self.assertEqual(report, advance(self.case, "M", report["saved"], self.protocol, self.prompts))

    def test_aggregate_uses_world_pairs_and_preserves_missing(self):
        cases = load_cases(PROTOCOL, "main")
        prototype = run_fixture(self.case, "M", self.protocol, self.prompts)
        reports = []
        for case in cases:
            for policy in POLICIES:
                report = deepcopy(prototype)
                report.update(case_id=case["case_id"], family=case["family"], policy=policy,
                              input_case_sha256=digest(case), packets=[], score=.4 if policy == "M" else .2)
                reports.append(report)
        result = aggregate(cases, reports, self.protocol, "SYNTHETIC_ANALYSIS_FIXTURE")
        self.assertEqual(result["primary_M_minus_R"]["n_worlds"], 60)
        self.assertAlmostEqual(result["primary_M_minus_R"]["mean_difference"], .2)
        self.assertTrue(result["primary_M_minus_R"]["degenerate_interval"])
        self.assertFalse(result["primary_M_minus_R"]["equivalence_established"])
        reports[0]["score"] = None
        incomplete = aggregate(cases, reports, self.protocol, "SYNTHETIC_ANALYSIS_FIXTURE")
        self.assertIsNone(incomplete["primary_M_minus_R"])
        self.assertEqual(len(incomplete["unresolved_results"]), 1)
        self.assertFalse(incomplete["is_llm_behavioral_evidence"])

    def test_all_planned_cases_remain_in_an_empty_result_table(self):
        result = aggregate(self.cases, [], self.protocol, "OFFLINE_ENGINE_FIXTURE")
        self.assertEqual(len(result["rows"]), 30)
        self.assertTrue(all(r["stop"] == "NOT_ATTEMPTED" for r in result["rows"]))
        self.assertEqual(result["policies"]["M"]["received"], 0)
        self.assertIsNone(result["primary_M_minus_R"])

    def test_batch_duplicate_response_ids_are_rejected(self):
        first = run_fixture(self.case, "M", self.protocol, self.prompts)
        second = run_fixture(self.case, "R", self.protocol, self.prompts)
        second["packets"][0]["outcome"]["response_id"] = first["packets"][0]["outcome"]["response_id"]
        with self.assertRaisesRegex(ValueError, "duplicate response ID"):
            aggregate(self.cases, [first, second], self.protocol, "OFFLINE_ENGINE_FIXTURE")


if __name__ == "__main__":
    unittest.main()
