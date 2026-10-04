"""Independent expectations for Experiment 03 fixtures, not LLM effect evidence."""

import json
import socket
import subprocess
from collections.abc import Iterator
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from typing import cast

import pytest

from journeymap.adapters.event_memory import (
    EventMemoryContext,
    EventTraceArchive,
    RecencyEventMemory,
)
from journeymap.adapters.horizon_baseline import PreviousPlaceProvider, sections
from journeymap.adapters.llm import LLMController, Record, event_memory_input, parse_candidate
from journeymap.adapters.memory_horizon_prompt import PROFILE as HORIZON_PROFILE
from journeymap.adapters.pickup_cue_prompt import (
    INSTRUCTIONS_SHA256,
    PROFILE,
    Fixture,
    FullPrefixMemory,
    PickupFixtureProvider,
    diagnostic_input,
)
from journeymap.adapters.provider import ProviderRequest
from journeymap.bootstrap import create_pickup_cue_application, create_pickup_cue_kernel
from journeymap.core.canonical import JsonObject, JsonValue, canonical_json
from journeymap.core.controller import GamePort
from journeymap.core.handlers import ActionRequest, ActionValidationError
from journeymap.core.kernel import SimulationKernel
from journeymap.core.observations import Observation
from journeymap.experiments.alderwick import export_trial
from journeymap.experiments.memory_horizon import run_trial
from journeymap.experiments.memory_horizon_audit import audit_export as horizon_audit
from journeymap.experiments.memory_horizon_audit import obj, rows
from journeymap.experiments.memory_horizon_audit import replay_export as horizon_replay
from journeymap.experiments.pickup_cue import (
    CONDITIONS,
    evaluate,
    export_case,
    run_case,
    run_matrix,
    seal,
)
from journeymap.experiments.pickup_cue_audit import (
    audit_export,
    audit_matrix,
    audit_record,
    read_export,
    reconstruct,
    replay_record,
)
from journeymap.modules.movement.handlers import MoveHandler


@pytest.fixture(scope="module", autouse=True)
def offline() -> Iterator[None]:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("network forbidden in Experiment 03 offline verification")

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(socket, "create_connection", forbidden)
        patch.setattr(socket.socket, "connect", forbidden)
        patch.setattr(socket.socket, "connect_ex", forbidden)
        yield


@pytest.fixture(scope="module")
def matrix(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[Path, dict[tuple[str, int, str], JsonObject]]:
    directory = tmp_path_factory.mktemp("pickup") / "matrix"
    summary = run_matrix(directory)
    assert summary["status"] == "INCLUDED"
    assert summary["case_count"] == summary["responded_denominator"] == 40
    assert summary["task_success_count"] == 20  # 2 * (4 + 3 + 2 + 1)
    records = {}
    for path in directory.iterdir():
        if path.is_dir():
            data = read_export(path)
            case = obj(data["case"])
            records[
                cast(str, case["target"]), cast(int, case["d"]), cast(str, case["condition"])
            ] = data
    return directory, records


@pytest.fixture(scope="module")
def baseline() -> Record:
    return run_trial(
        LLMController(
            PreviousPlaceProvider(),
            model="previous-place-reference-1",
            event_memory=RecencyEventMemory(1),
            prompt_profile=HORIZON_PROFILE,
        ),
        trial_id="previous-place-reference",
    )


def test_baseline_exact_engine_outcome_and_replay(baseline: Record, tmp_path: Path) -> None:
    data = baseline.data
    assert data["metrics"] == {
        "unique_destination_coverage": 3,
        "repeat_destination_count": 0,
        "task_completed": True,
        "decisions_to_completion": 6,
        "decision_attempt_count": 18,
        "engine_submission_count": 18,
        "strict_no_repeat_success": True,
        "post_completion_repeat_count": 0,
    }
    actions = rows(data["action_requests"])
    assert [a["action_type"] for a in actions] == ["MOVE"] * 6 + ["WAIT"] * 12
    assert [obj(a["payload"]).get("duration") for a in actions[6:]] == [1] * 12
    assert rows(data["action_results"])[5]["resolved_at"] == 12
    directory = tmp_path / "baseline"
    export_trial(baseline, directory)
    assert horizon_replay(directory).final_simulation_time == 24
    assert horizon_audit(directory)["status"] == "INCLUDED"
    identity = obj(obj(data["manifest"])["provider_identity"])
    assert (
        identity["kind"] == "fixture"
        and identity["version"] == "memory-horizon-previous-place-fixture-1"
    )


def test_baseline_is_stateless_and_ignores_metadata(baseline: Record) -> None:
    decisions = rows(baseline.data["decisions"])
    provider = PreviousPlaceProvider()
    requests = [ProviderRequest(**obj(d["provider_request"])) for d in decisions]  # type: ignore[arg-type]
    expected = [provider.generate(r).text for r in requests]
    for i in reversed(range(len(requests))):
        assert (
            provider.generate(requests[i]).text
            == PreviousPlaceProvider().generate(requests[i]).text
            == expected[i]
        )
        inputs = json.loads(requests[i].prompt.split("\nINPUT_JSON\n")[1])
        inputs.update({"condition": "canary", "tick": 999, "target": "well", "run_id": "other"})
        inputs["observation"].update({"simulation_time": 100, "sequence": 999, "digest": "canary"})
        changed = replace(requests[i], prompt=HORIZON_PROFILE.render(inputs))
        assert provider.generate(changed).text == expected[i]
    bad = json.loads(requests[0].prompt.split("\nINPUT_JSON\n")[1])
    sections(bad["observation"])["local"]["local"] = {"exits": []}
    response = provider.generate(replace(requests[0], prompt=HORIZON_PROFILE.render(bad)))
    assert response.metadata["incomplete_details"] == {"reason": "BASELINE_INPUT_PRECONDITION"}


def test_all_prefixes_and_handwritten_cue_table(
    matrix: tuple[Path, dict[tuple[str, int, str], JsonObject]],
) -> None:
    table = {
        1: [False, True, True, True, True],
        2: [False, False, True, True, True],
        3: [False, False, False, True, True],
        4: [False, False, False, False, True],
    }
    for (target, d, condition), data in matrix[1].items():
        prefix = rows(data["prefix"])
        assert len(prefix) == 5
        assert [obj(s["request"])["action_type"] for s in prefix] == ["WAIT"] * (4 - d) + [
            "MOVE",
            "MOVE",
        ] + ["WAIT"] * (d - 1)
        assert all(
            obj(s["receipt"])["status"] == "SUCCEEDED" and s["engine_submitted"] is True
            for s in prefix
        )
        assert obj(data["checkpoint"])["tick"] == 7
        assert data["knowledge"] == data["initial_knowledge"] == []
        cues = [
            i
            for i, s in enumerate(prefix, 1)
            if sections(obj(s["observation"]))["pickup_notice"]["pickup_notice"] is not None
        ]
        assert cues == [6 - d]
        assert all(sections(obj(s["observation"]))["records"] == {"records": []} for s in prefix)
        decision = obj(data["decision"])
        inputs = obj(decision["input"])
        index = CONDITIONS.index(condition)
        assert inputs["retrieved_count"] == [0, 1, 2, 3, 5][index]
        assert inputs["cue_present"] is table[d][index]
        assert obj(data["evaluation"])["task_success"] is table[d][index]
        assert obj(data["evaluation"])["action_type"] == ("MOVE" if table[d][index] else "WAIT")
        assert decision["provider_called"] is True
        assert len(rows(data["action_traces"])) == len(rows(data["event_traces"])) == 6
        assert decision["closed_trace_id"] not in cast(
            list[str], inputs["retrieved_event_trace_ids"]
        )
        assert obj(obj(obj(data["checkpoint"])["state"])["pickup_cue"])["pickup_location"] == target


def test_full_request_equivalence_and_only_notice_difference(
    matrix: tuple[Path, dict[tuple[str, int, str], JsonObject]],
) -> None:
    current = set()
    configurations = set()
    for (_, d, condition), data in matrix[1].items():
        evidence = obj(obj(data["decision"])["input"])
        current.add(cast(str, evidence["current_canonical"]))
        request = obj(evidence["provider_request"])
        configurations.add(canonical_json({k: v for k, v in request.items() if k != "prompt"}))
        other = obj(obj(matrix[1]["bakery", d, condition]["decision"])["input"])
        left, right = canonical_json(request), canonical_json(other["provider_request"])
        if evidence["cue_present"] is False:
            assert left == right
        elif obj(data["case"])["target"] == "inn":
            # Change exactly the single historical cue. All routes/current facts remain intact.
            semantic = Record.capture(obj(evidence["model_visible_input"])).data
            changed = 0
            for event in rows(semantic["event_memory"]):
                notice = sections(obj(event["observation"]))["pickup_notice"]
                if notice["pickup_notice"] is not None:
                    notice["pickup_notice"] = {"pickup_location": "bakery"}
                    changed += 1
            assert changed == 1
            assert {**request, "prompt": PROFILE.render(semantic)} == other["provider_request"]
        prompt = cast(str, request["prompt"])
        assert all(
            forbidden not in prompt
            for forbidden in (
                '"simulation_time"',
                '"sequence"',
                '"run_id"',
                '"condition"',
                '"d"',
                '"target"',
                '"digest"',
                '"case_id"',
                '"source"',
                '"runtime"',
                '"event_trace_id"',
            )
        )
    assert len(current) == len(configurations) == 1


def test_fixture_uses_only_rendered_cue(
    matrix: tuple[Path, dict[tuple[str, int, str], JsonObject]],
) -> None:
    data = matrix[1]["inn", 4, "full-prefix"]
    inputs = obj(obj(data["decision"])["input"])
    request = ProviderRequest(**obj(inputs["provider_request"]))  # type: ignore[arg-type]
    semantic = Record.capture(obj(inputs["model_visible_input"])).data
    for event in rows(semantic["event_memory"]):
        notice = sections(obj(event["observation"]))["pickup_notice"]
        if notice["pickup_notice"] is not None:
            notice["pickup_notice"] = {"pickup_location": "bakery"}
    response = PickupFixtureProvider(Fixture()).generate(
        replace(request, prompt=PROFILE.render(semantic))
    )
    assert json.loads(response.text)["payload"] == {"route_id": "village-square-to-bakery"}
    semantic["event_memory"] = []
    response = PickupFixtureProvider(Fixture()).generate(
        replace(request, prompt=PROFILE.render(semantic))
    )
    assert json.loads(response.text) == {"action_type": "WAIT", "payload": {"duration": 1}}
    assert obj(inputs["model_visible_input"])["event_memory"] != []


@pytest.mark.parametrize(
    "text,failure,execution,success",
    [
        (
            '{"action_type":"MOVE","payload":{"route_id":"village-square-to-inn"}}',
            None,
            "SUCCEEDED",
            True,
        ),
        (
            '{"action_type":"MOVE","payload":{"route_id":"village-square-to-bakery"}}',
            None,
            "SUCCEEDED",
            False,
        ),
        ('{"action_type":"WAIT","payload":{"duration":1}}', None, "SUCCEEDED", False),
        ('{"action_type":"MOVE","payload":{"route_id":"missing"}}', None, "REJECTED", False),
        ("bad json", "INVALID_OUTPUT", "NOT_SUBMITTED", False),
        (
            '{"action_type":"WAIT","action_type":"WAIT","payload":{"duration":1}}',
            "INVALID_OUTPUT",
            "NOT_SUBMITTED",
            False,
        ),
        (
            '{"action_type":"WAIT","payload":{"duration":NaN}}',
            "INVALID_OUTPUT",
            "NOT_SUBMITTED",
            False,
        ),
        (
            '{"action_type":"WAIT","payload":{"duration":true}}',
            "INVALID_OUTPUT",
            "NOT_SUBMITTED",
            False,
        ),
        (
            '{"action_type":"WAIT","payload":{"duration":2}}',
            "INVALID_OUTPUT",
            "NOT_SUBMITTED",
            False,
        ),
        (
            '{"action_type":"REST","payload":{"duration":1}}',
            "INVALID_OUTPUT",
            "NOT_SUBMITTED",
            False,
        ),
        (
            '{"action_type":"WAIT","payload":{"duration":1},"actor_id":"hugh"}',
            "INVALID_OUTPUT",
            "NOT_SUBMITTED",
            False,
        ),
        ("\ud800", "INVALID_OUTPUT", "NOT_SUBMITTED", False),
    ],
)
def test_one_response_failure_contract(
    text: str, failure: str | None, execution: str, success: bool
) -> None:
    data = run_case(
        case_id="fixed", target="inn", d=2, condition="full-prefix", fixture=Fixture("fixed", text)
    ).data
    result = obj(data["evaluation"])
    assert (result["failure"], result["execution"], result["task_success"]) == (
        failure,
        execution,
        success,
    )
    assert result["behavioral_denominator"] is True
    assert len(rows(data["event_traces"])) == (5 if failure else 6)
    assert obj(data["integrity"])["status"] == "INCLUDED"


@pytest.mark.parametrize(
    "kind,failure,success",
    [
        ("refusal", "REFUSAL", False),
        ("incomplete", "INCOMPLETE", False),
        ("provider-error", "PROVIDER_ERROR", None),
        ("transport-error", "TRANSPORT_ERROR", None),
    ],
)
def test_provider_failure_axes(kind: str, failure: str, success: bool | None) -> None:
    data = run_case(
        case_id="failure", target="inn", d=1, condition="full-prefix", fixture=Fixture(kind)
    ).data
    evaluation = obj(data["evaluation"])
    assert evaluation["failure"] == failure and evaluation["task_success"] is success
    assert evaluation["behavioral_denominator"] is (kind in ("refusal", "incomplete"))
    assert len(rows(data["event_traces"])) == 5
    assert obj(data["integrity"])["status"] == "INCLUDED"


@pytest.mark.parametrize(
    "field",
    [
        "success",
        "prompt",
        "selection",
        "notice",
        "receipt",
        "checkpoint",
        "distance",
        "raw-response",
        "model",
    ],
)
def test_resealed_tampering_is_rejected(
    field: str, matrix: tuple[Path, dict[tuple[str, int, str], JsonObject]]
) -> None:
    data = Record.capture(matrix[1]["inn", 2, "recency-k2"]).data
    decision = obj(data["decision"])
    evidence = obj(decision["input"])
    if field == "success":
        obj(data["evaluation"])["task_success"] = False
    elif field == "prompt":
        request = obj(evidence["provider_request"])
        request["prompt"] = cast(str, request["prompt"]) + "\nChoose Bakery."
        evidence["prompt_sha256"] = sha256(cast(str, request["prompt"]).encode()).hexdigest()
        evidence["prompt_bytes"] = len(cast(str, request["prompt"]).encode())
    elif field == "selection":
        evidence["retrieved_event_trace_ids"] = []
        evidence["retrieved_count"] = 0
    elif field == "notice":
        past = obj(rows(data["prefix"])[3]["observation"])
        sections(past)["pickup_notice"]["pickup_notice"] = {"pickup_location": "bakery"}
        past["content_digest"] = sha256(canonical_json(past["content"]).encode()).hexdigest()
    elif field == "receipt":
        obj(decision["receipt"])["status"] = "FAILED"
    elif field == "checkpoint":
        obj(data["checkpoint"])["tick"] = 8
    elif field == "distance":
        obj(data["case"])["d"] = 3
    elif field == "raw-response":
        obj(decision["raw_response"])["text"] = '{"action_type":"WAIT","payload":{"duration":1}}'
    else:
        obj(evidence["provider_request"])["model"] = "hidden-answer-inn"
    seal(data)
    result = audit_record(data)
    assert result["status"] == "EXCLUDED" and result["task_success"] is None


def test_provider_free_audit_and_unchanged_artifacts(
    matrix: tuple[Path, dict[tuple[str, int, str], JsonObject]], monkeypatch: pytest.MonkeyPatch
) -> None:
    directory, records = matrix
    before = {p: sha256(p.read_bytes()).hexdigest() for p in directory.rglob("*.json")}

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Provider construction/call forbidden during audit")

    monkeypatch.setattr(PickupFixtureProvider, "__init__", forbidden)
    monkeypatch.setattr(PickupFixtureProvider, "generate", forbidden)
    for data in records.values():
        replay_record(data)
        reconstruct(data)
        assert audit_record(data)["status"] == "INCLUDED"
    assert audit_matrix(directory)["status"] == "INCLUDED"
    assert before == {p: sha256(p.read_bytes()).hexdigest() for p in before}


def test_no_live_provider_injection() -> None:
    class LabeledLive:
        kind = "fixture"

        def generate(self, request: ProviderRequest) -> None:
            pytest.fail("must reject before call")

    with pytest.raises(TypeError):
        run_case(case_id="live", target="inn", d=1, condition="full-prefix", fixture=LabeledLive())  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        PickupFixtureProvider(LabeledLive())  # type: ignore[arg-type]


def test_preparation_and_evaluation_observation_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    original = GamePort.observe
    for fail_at in (1, 6):
        calls = 0

        def observe(game: GamePort, failure_at: int = fail_at) -> Observation:
            nonlocal calls
            calls += 1
            if calls == failure_at:
                raise RuntimeError("private details")
            return original(game)

        with monkeypatch.context() as patch:
            patch.setattr(GamePort, "observe", observe)
            data = run_case(
                case_id="observation-error", target="inn", d=1, condition="full-prefix"
            ).data
        assert obj(data["decision"])["provider_called"] is False
        assert obj(data["evaluation"])["task_success"] is None
        assert "private details" not in Record.capture(data).serialized
        if fail_at == 1:
            assert data["prefix_status"] == "PREPARATION_FAILED"
            assert rows(data["prefix"])[0]["failure"] == "OBSERVATION_ERROR"
        else:
            assert obj(data["decision"])["failure"] == "OBSERVATION_ERROR"


def test_engine_and_recording_failure_retains_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    original = GamePort.submit

    def fail(game: GamePort, request: ActionRequest) -> object:
        if request.submitted_at == 7:
            raise RuntimeError("private engine details")
        return original(game, request)

    with monkeypatch.context() as patch:
        patch.setattr(GamePort, "submit", fail)
        data = run_case(case_id="engine-error", target="inn", d=1, condition="full-prefix").data
    assert obj(data["decision"])["failure"] == "ENGINE_ERROR"
    assert obj(data["evaluation"])["task_success"] is None
    assert len(rows(data["event_traces"])) == 5
    close = EventTraceArchive.close

    def recording_error(self: EventTraceArchive, *args: object, **kwargs: object) -> object:
        if len(self.traces) == 5:
            raise RuntimeError("recording defect")
        return close(self, *args, **kwargs)  # type: ignore[arg-type]

    with monkeypatch.context() as patch:
        patch.setattr(EventTraceArchive, "close", recording_error)
        data = run_case(case_id="recording-error", target="inn", d=1, condition="full-prefix").data
    assert obj(data["decision"])["failure"] == "RECORDING_ERROR"
    assert obj(data["integrity"])["status"] == "EXCLUDED"
    assert obj(data["evaluation"])["task_success"] is None
    assert len(rows(data["action_requests"])) == 6


def test_failed_engine_move_never_counts_as_arrival(monkeypatch: pytest.MonkeyPatch) -> None:
    original = MoveHandler.validate_completion

    def failed(self: MoveHandler, request: ActionRequest, *args: object) -> None:
        if request.submitted_at == 7:
            raise ActionValidationError("ROUTE_BLOCKED")
        original(self, request, *args)  # type: ignore[arg-type]

    monkeypatch.setattr(MoveHandler, "validate_completion", failed)
    data = run_case(case_id="failed-move", target="inn", d=1, condition="full-prefix").data
    assert obj(data["evaluation"])["execution"] == "FAILED"
    assert evaluate(data)["task_success"] is False


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "mislinked"])
def test_matrix_inventory_rejects_corruption(
    mutation: str, matrix: tuple[Path, dict[tuple[str, int, str], JsonObject]], tmp_path: Path
) -> None:
    import shutil

    directory = tmp_path / "copy"
    shutil.copytree(matrix[0], directory)
    path = directory / "matrix.json"
    data = json.loads(path.read_text())
    if mutation == "missing":
        data["cases"].pop()
    elif mutation == "duplicate":
        data["cases"][-1] = data["cases"][0]
    else:
        data["cases"][0]["case_id"] = data["cases"][1]["case_id"]
    path.write_text(json.dumps(data), encoding="utf-8")
    assert audit_matrix(directory)["status"] == "EXCLUDED"


def test_export_never_overwrites(
    matrix: tuple[Path, dict[tuple[str, int, str], JsonObject]], tmp_path: Path
) -> None:
    record = Record.capture(matrix[1]["inn", 1, "full-prefix"])
    directory = tmp_path / "export"
    export_case(record, directory)
    before = (directory / "case.json").read_bytes()
    with pytest.raises(FileExistsError):
        export_case(record, directory)
    assert (directory / "case.json").read_bytes() == before
    assert audit_export(directory)["status"] == "INCLUDED"


def test_trace_isolation_projection_detachment_and_contributor_guard() -> None:
    kernel = create_pickup_cue_kernel(run_id="isolation", target="inn")
    kernel.boot()
    try:
        app, _ = create_pickup_cue_application(kernel)
        game, archive = app.game_for("stranger"), EventTraceArchive()
        first = game.observe()
        archive.begin(first)
        action = parse_candidate({"action_type": "WAIT", "payload": {"duration": 1}}, first)
        trace = archive.close(first, action, game.submit(action), engine_submitted=True)
        current = game.observe()
        context = archive.begin(current)
        raw, _ = event_memory_input(context, RecencyEventMemory(1))
        frozen = Record.capture(raw).serialized
        projected = diagnostic_input(raw)
        projected["event_memory"] = []
        assert Record.capture(raw).serialized == frozen
        for bad in (
            replace(trace, actor_id="hugh"),
            replace(trace, run_id="other"),
            replace(trace, closed_at=100),
            replace(trace, opportunity_sequence=2),
            replace(trace, memory_eligible=False),
        ):
            with pytest.raises(ValueError):
                EventMemoryContext(current, context.decision_opportunity_id, 2, 1, (bad,))
        with pytest.raises(ValueError):
            FullPrefixMemory().select_events(context)
        sections(obj(raw["observation"]))["records"]["records"] = [{"answer": "inn"}]
        with pytest.raises(ValueError, match="Knowledge"):
            diagnostic_input(raw)
    finally:
        kernel.close()


@pytest.mark.parametrize(
    "location,previous,destination",
    [
        ("inn", None, "village-square"),
        ("bakery", "well", "village-square"),
        ("well", "inn", "village-square"),
        ("village-square", None, "inn"),
        ("village-square", "inn", "bakery"),
        ("village-square", "bakery", "well"),
        ("village-square", "well", None),
        ("village-square", "village-square", None),
    ],
)
def test_baseline_handwritten_table(
    location: str, previous: str | None, destination: str | None
) -> None:
    def semantic(place: str) -> JsonObject:
        return {
            "content": {
                "sections": [
                    {"contributor_id": "position", "content": {"position": {"location_id": place}}},
                    {
                        "contributor_id": "local",
                        "content": {
                            "local": {
                                "exits": [
                                    {"destination": dest, "route_id": f"visible-{dest}"}
                                    for dest in ("inn", "bakery", "well", "village-square")
                                ]
                            }
                        },
                    },
                ]
            }
        }

    inputs: JsonObject = {
        "observation": semantic(location),
        "event_memory": ([] if previous is None else [{"observation": semantic(previous)}]),
    }
    request = ProviderRequest(
        HORIZON_PROFILE.render(inputs), HORIZON_PROFILE.version, "fixture", "fixture"
    )
    action = json.loads(PreviousPlaceProvider().generate(request).text)
    assert action == (
        {"action_type": "MOVE", "payload": {"route_id": f"visible-{destination}"}}
        if destination
        else {"action_type": "WAIT", "payload": {"duration": 1}}
    )


def test_invalid_raw_utf8_export_remains_auditable(tmp_path: Path) -> None:
    record = run_case(
        case_id="surrogate",
        target="inn",
        d=1,
        condition="full-prefix",
        fixture=Fixture("fixed", "\ud800"),
    )
    directory = tmp_path / "surrogate"
    export_case(record, directory)
    assert audit_export(directory)["status"] == "INCLUDED"
    assert obj(obj(read_export(directory)["decision"])["raw_response"])["text"] == "\ud800"


def test_fresh_owned_providers_and_zero_prefix_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    providers: list[PickupFixtureProvider] = []
    starts: list[int] = []
    submits = 0
    original_init, original_generate, original_submit = (
        PickupFixtureProvider.__init__,
        PickupFixtureProvider.generate,
        GamePort.submit,
    )

    def init(self: PickupFixtureProvider, fixture: Fixture) -> None:
        original_init(self, fixture)
        providers.append(self)
        starts.append(submits)

    def submit(self: GamePort, request: ActionRequest) -> object:
        nonlocal submits
        submits += 1
        return original_submit(self, request)

    def generate(self: PickupFixtureProvider, request: ProviderRequest) -> object:
        assert len(self.calls) == 0
        assert submits - starts[-1] == 5
        return original_generate(self, request)

    monkeypatch.setattr(PickupFixtureProvider, "__init__", init)
    monkeypatch.setattr(PickupFixtureProvider, "generate", generate)
    monkeypatch.setattr(GamePort, "submit", submit)
    for target in ("inn", "bakery"):
        run_case(case_id=target, target=target, d=2, condition="full-prefix")
    assert len(providers) == 2 and providers[0] is not providers[1]
    assert providers[0].calls is not providers[1].calls
    assert all(len(provider.calls) == 1 for provider in providers)


@pytest.mark.parametrize("bad", ["hidden-notice", "notice-extra", "local-extra", "section-extra"])
def test_projection_rejects_unreviewed_contributor_data(
    bad: str, matrix: tuple[Path, dict[tuple[str, int, str], JsonObject]]
) -> None:
    data = Record.capture(matrix[1]["inn", 1, "full-prefix"]).data
    raw: JsonObject = {
        "observation": obj(data["decision"])["observation"],
        "event_memory": cast(list[JsonValue], rows(data["event_traces"])[:5]),
    }
    parts = sections(obj(raw["observation"]))
    if bad == "hidden-notice":
        parts["pickup_notice"]["pickup_notice"] = {"pickup_location": "inn"}
    elif bad == "notice-extra":
        parts["pickup_notice"]["hidden"] = "inn"
    elif bad == "local-extra":
        obj(parts["local"]["local"])["answer"] = "inn"
    else:
        rows(obj(obj(raw["observation"])["content"])["sections"])[0]["target"] = "inn"
    with pytest.raises(ValueError):
        diagnostic_input(raw)


def test_cli_offline_only_and_fixed_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from journeymap.examples.pickup_cue import main

    with monkeypatch.context() as patch:
        patch.setattr("sys.argv", ["pickup_cue", "--live"])
        with pytest.raises(SystemExit) as error:
            main()
        assert error.value.code == 2
    directory = tmp_path / "cli"
    monkeypatch.setattr(
        "sys.argv",
        [
            "pickup_cue",
            "--case-id",
            "cli",
            "--target",
            "inn",
            "--distance",
            "2",
            "--memory",
            "full-prefix",
            "--fixture",
            "wait",
            "--output",
            str(directory),
        ],
    )
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 0
    assert obj(read_export(directory)["evaluation"])["task_success"] is False


def test_missing_or_duplicate_export_fields_are_excluded(
    matrix: tuple[Path, dict[tuple[str, int, str], JsonObject]], tmp_path: Path
) -> None:
    data = Record.capture(matrix[1]["inn", 1, "full-prefix"]).data
    del data["event_traces"]
    seal(data)
    assert audit_record(data)["status"] == "EXCLUDED"
    directory = tmp_path / "duplicate"
    directory.mkdir()
    (directory / "case.json").write_text(
        '{"export_version":"pickup-cue-offline-record-1","export_version":"pickup-cue-offline-record-1"}',
        encoding="utf-8",
    )
    assert audit_export(directory)["status"] == "EXCLUDED"


def test_prompt_lf_and_fixed_bytes() -> None:
    assert "\r" not in PROFILE.instructions
    assert PROFILE.instructions.endswith("with no duplicate keys.\n")
    assert len(PROFILE.instructions.encode("utf-8")) == 1027
    assert INSTRUCTIONS_SHA256 == "a250e897c928f06d25939700c6f65e4895a3729fd38a355186208ebd5e88f1b4"


def test_checkout_identity_records_detached_head_as_null() -> None:
    branch = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    data = run_case(case_id="checkout", target="inn", d=1, condition="full-prefix").data
    assert obj(data["source"])["branch"] == (branch or None)
    assert audit_record(data)["status"] == "INCLUDED"


@pytest.mark.parametrize("branch", [None, "codex/named-checkout"])
def test_audit_accepts_named_and_detached_provenance(branch: JsonValue) -> None:
    data = run_case(case_id="provenance", target="inn", d=1, condition="full-prefix").data
    obj(data["source"])["branch"] = branch
    seal(data)
    assert audit_record(data)["status"] == "INCLUDED"


@pytest.mark.parametrize(
    "field,value",
    [
        ("branch", ""),
        ("branch", " "),
        ("branch", 1),
        ("git_commit", None),
        ("working_source_sha256", None),
    ],
)
def test_audit_rejects_invalid_checkout_provenance(field: str, value: JsonValue) -> None:
    data = run_case(case_id="provenance", target="inn", d=1, condition="full-prefix").data
    obj(data["source"])[field] = value
    seal(data)
    assert audit_record(data)["status"] == "EXCLUDED"


def test_audit_requires_explicit_branch_field() -> None:
    data = run_case(case_id="provenance", target="inn", d=1, condition="full-prefix").data
    del obj(data["source"])["branch"]
    seal(data)
    assert audit_record(data)["status"] == "EXCLUDED"


def test_unknown_engine_outcome_is_not_behavioral_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    original = SimulationKernel.submit_action

    def interrupted(self: SimulationKernel, request: ActionRequest) -> object:
        if request.submitted_at == 7:
            raise RuntimeError("engine interrupted before a recorded result")
        return original(self, request)

    with monkeypatch.context() as patch:
        patch.setattr(SimulationKernel, "submit_action", interrupted)
        data = run_case(case_id="unknown", target="inn", d=1, condition="full-prefix").data
    evaluation = obj(data["evaluation"])
    assert evaluation["execution"] == "UNKNOWN"
    assert evaluation["task_success"] is None
    assert evaluation["behavioral_denominator"] is True
    assert obj(data["decision"])["engine_submitted"] is True
    assert len(rows(data["event_traces"])) == 5
    assert audit_record(data)["status"] == "EXCLUDED"
