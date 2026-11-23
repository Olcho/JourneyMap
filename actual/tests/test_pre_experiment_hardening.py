"""Offline provenance, admission and timing regressions; no live transport."""

import json
import platform
import sys
import tomllib
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from typing import cast

import pytest

from journeymap import __version__
from journeymap.adapters.llm import LLMController, Record
from journeymap.adapters.openai_provider import OpenAIProvider
from journeymap.adapters.provider import (
    ProviderIdentity,
    ProviderRequest,
    RawModelResponse,
    provider_identity,
    response_identity_matches,
)
from journeymap.core.canonical import JsonObject, JsonValue
from journeymap.core.handlers import ActionRequest, ActionTiming, ValidationContext
from journeymap.core.replay import ReplayHarness, ReplayInput, ReplayReport
from journeymap.examples.alderwick_llm import ProtocolFakeProvider
from journeymap.experiments.alderwick import (
    BudgetMonitor,
    action_duration,
    audit_export,
    export_trial,
    replay_export,
    run_trial,
)
from journeymap.experiments.inclusion import assess_inclusion, record_digest
from journeymap.experiments.provenance import code_identity, logical_source_sha256, runtime_identity
from journeymap.modules.movement.handlers import MoveHandler
from journeymap.scenarios.alderwick.resources import resource_world

ROOT = Path(__file__).resolve().parents[1]


def obj(value: JsonValue) -> JsonObject:
    assert isinstance(value, dict)
    return value


@pytest.fixture(scope="module")
def hardened_trial() -> Record:
    return run_trial(
        LLMController(ProtocolFakeProvider(), model="fixture"), trial_id="hardening-regression"
    )


def test_package_engine_runtime_and_configuration(hardened_trial: Record) -> None:
    package = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    manifest = obj(hardened_trial.data["manifest"])
    assert __version__ == package["project"]["version"] == "0.1.0"
    assert obj(manifest["engine_manifest"])["engine_version"] == __version__
    assert (
        manifest["python_runtime"]
        == runtime_identity()
        == {
            "implementation": platform.python_implementation(),
            "version": list(sys.version_info[:3]),
        }
    )
    assert manifest["configuration_version"] == "responses-structured-2"
    assert manifest["observation_budget_bytes"] == 65536
    assert obj(manifest["termination_policy"])["wall_timeout_seconds"] == 300
    identity = obj(manifest["provider_identity"])
    assert identity["name"] == "fake" and identity["kind"] == "fixture"
    assert identity["implementation"].endswith(".ProtocolFakeProvider")  # type: ignore[union-attr]
    assert obj(manifest["code"])["source_identity_version"] == "utf8-lf-path-lengths-1"


def test_provider_socket_timeout_is_recorded_without_environment_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("must not access credentials or transport")

    monkeypatch.setattr("journeymap.adapters.openai_provider.os.environ", {})
    monkeypatch.setattr(OpenAIProvider, "generate", forbidden)
    controller = LLMController(OpenAIProvider(timeout_seconds=17))
    assert controller.provider_identity["timeout_seconds"] == 17
    trial = run_trial(
        controller, trial_id="no-observation", monitor=BudgetMonitor(max_content_bytes=1)
    )
    manifest = obj(trial.data["manifest"])
    assert manifest["provider_calls"] == 0
    assert obj(manifest["provider_identity"])["timeout_seconds"] == 17
    assert manifest["observation_budget_bytes"] == 1


def test_source_identity_normalizes_only_line_endings_and_orders_utf8_paths() -> None:
    lf = {"src/journeymap/한.py": 'value = "한글"\n'.encode(), "pyproject.toml": b"x=1\n"}
    crlf = {name: value.replace(b"\n", b"\r\n") for name, value in reversed(list(lf.items()))}
    assert logical_source_sha256(lf) == logical_source_sha256(crlf)
    for changed in (b"x=2\n", b"x =1\n", b"x=1", b"x=1\r", b"\xef\xbb\xbfx=1\n"):
        assert logical_source_sha256(lf) != logical_source_sha256({**lf, "pyproject.toml": changed})
    assert logical_source_sha256({"a": b"bc"}) != logical_source_sha256({"ab": b"c"})
    with pytest.raises(UnicodeDecodeError):
        logical_source_sha256({"a.py": b"\xff"})


def test_legacy_raw_hash_and_working_identity_are_separate(tmp_path: Path) -> None:
    source = tmp_path / "src" / "journeymap" / "sample.py"
    source.parent.mkdir(parents=True)
    (tmp_path / "pyproject.toml").write_bytes(b'version="0.1.0"\n')
    source.write_bytes(b"x=1\r\n")
    windows = code_identity(tmp_path)
    assert windows["source_sha256"] == sha256(b"src/journeymap/sample.py\0x=1\r\n").hexdigest()
    source.write_bytes(b"x=1\n")
    unix = code_identity(tmp_path)
    assert windows["source_sha256"] != unix["source_sha256"]
    assert windows["working_source_sha256"] == unix["working_source_sha256"]
    source.write_bytes(b"x=2\n")
    assert code_identity(tmp_path)["working_source_sha256"] != unix["working_source_sha256"]
    actual = code_identity()
    assert actual["git_commit"] and actual["git_source_tree"]
    assert type(actual["working_tree_dirty"]) is bool


@pytest.mark.parametrize("labels", [{"provider_name": "openai"}, {"provider_version": "live"}])
def test_fake_cannot_be_relabelled_by_caller(labels: dict[str, str]) -> None:
    provider = ProtocolFakeProvider()
    with pytest.raises(ValueError, match="differs from adapter"):
        run_trial(
            LLMController(provider),
            trial_id="mislabel",
            provider_name=labels.get("provider_name"),
            provider_version=labels.get("provider_version"),
        )
    assert provider.calls == 0


def test_arbitrary_name_attributes_do_not_turn_an_unidentified_provider_into_live() -> None:
    class Mislabelled:
        name = "openai"
        version = "responses-http-2"

        def generate(self, request: ProviderRequest) -> RawModelResponse:
            raise AssertionError("not called")

    identity = provider_identity(Mislabelled())
    assert identity["name"] == "fake" and identity["kind"] == "fixture"
    with pytest.raises(ValueError):
        ProviderIdentity("openai", "fixture-1")


@pytest.mark.parametrize(
    "metadata",
    [
        {"provider": "openai"},
        {"adapter_version": "wrong-version"},
        {"model": "wrong-model"},
    ],
)
def test_response_identity_mismatch_is_preserved_and_cannot_submit(metadata: JsonObject) -> None:
    class WrongResponse(ProtocolFakeProvider):
        def generate(self, request: ProviderRequest) -> RawModelResponse:
            raw = super().generate(request)
            return RawModelResponse(raw.text, {**raw.metadata, **metadata})

    trial = run_trial(LLMController(WrongResponse(), model="fixture"), trial_id="wrong-response")
    data = trial.data
    decision = obj(cast(list[JsonValue], data["decisions"])[0])
    assert decision["failure"] == "PROVIDER_IDENTITY_MISMATCH"
    assert decision["raw_output"] and decision["provider_identity_consistent"] is False
    assert data["action_requests"] == [] and data["initial_state"] == data["final_state"]
    assert obj(obj(data["manifest"])["research_inclusion"])["status"] == "EXCLUDED"


def test_live_response_requires_identity_and_accepts_same_alias_dated_snapshot() -> None:
    identity = provider_identity(OpenAIProvider())
    metadata: JsonObject = {
        "provider": "openai",
        "adapter_version": OpenAIProvider.version,
        "model": "alias",
    }
    assert response_identity_matches(identity, "alias", metadata)
    assert not response_identity_matches(identity, "alias", {})
    assert response_identity_matches(identity, "alias", {**metadata, "model": "alias-2026-09-14"})


def test_completed_replay_mismatch_is_excluded_and_record_preserved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = ReplayHarness.run

    def mismatch(self: ReplayHarness, replay_input: ReplayInput) -> ReplayReport:
        return replace(original(self, replay_input), final_state_digest="mismatched")

    monkeypatch.setattr(ReplayHarness, "run", mismatch)
    trial = run_trial(LLMController(ProtocolFakeProvider()), trial_id="replay-mismatch")
    manifest = obj(trial.data["manifest"])
    assert manifest["status"] == "COMPLETED" and manifest["replay_equal"] is False
    admission = obj(manifest["research_inclusion"])
    assert admission["status"] == "EXCLUDED" and "REPLAY_MISMATCH" in cast(
        list[str], admission["reasons"]
    )
    assert trial.data["action_traces"] and trial.data["engine_report"]


def test_valid_mistakes_and_engine_rejections_remain_included(hardened_trial: Record) -> None:
    data = hardened_trial.data
    assert obj(data["metrics"])["engine_statuses"] == {"SUCCEEDED": 20, "REJECTED": 2}
    assert assess_inclusion(data) == {
        "policy_version": "pre-experiment-1",
        "status": "INCLUDED",
        "reasons": [],
    }


@pytest.mark.parametrize("transport_error", [False, True])
def test_recovered_invalid_output_is_allowed_but_recovered_transport_failure_is_excluded(
    transport_error: bool,
) -> None:
    class Recovering(ProtocolFakeProvider):
        first = True

        def generate(self, request: ProviderRequest) -> RawModelResponse:
            if self.first:
                self.first = False
                if transport_error:
                    raise TimeoutError("unlogged transport detail")
                return RawModelResponse("invalid model JSON")
            return super().generate(request)

    data = run_trial(LLMController(Recovering()), trial_id="recovering").data
    manifest = obj(data["manifest"])
    assert manifest["status"] == "COMPLETED" and manifest["replay_equal"] is True
    assert obj(manifest["research_inclusion"])["status"] == (
        "EXCLUDED" if transport_error else "INCLUDED"
    )
    assert "unlogged transport detail" not in json.dumps(data)


@pytest.mark.parametrize(
    "mutation,reason",
    [
        ("trace", "ACTION_TRACE_INCOMPLETE"),
        ("config", "CONFIGURATION_INTEGRITY"),
        ("provider", "PROVIDER_IDENTITY_INCONSISTENCY"),
        ("source", "PROVENANCE_INCONSISTENCY"),
        ("runtime", "PROVENANCE_INCONSISTENCY"),
    ],
)
def test_gate_rechecks_structure_even_after_resealing(
    hardened_trial: Record, mutation: str, reason: str
) -> None:
    data = hardened_trial.data
    manifest = obj(data["manifest"])
    if mutation == "trace":
        cast(list[JsonValue], data["action_traces"]).pop()
    elif mutation == "config":
        manifest["model"] = "different-model"
    elif mutation == "provider":
        obj(manifest["provider_identity"])["name"] = "openai"
    elif mutation == "source":
        manifest["source_unchanged_during_trial"] = False
    else:
        obj(manifest["python_runtime"])["version"] = [3, 12]
    manifest["record_sha256"] = record_digest(data)
    result = assess_inclusion(data)
    assert result["status"] == "EXCLUDED" and reason in cast(list[str], result["reasons"])


@pytest.mark.parametrize(
    "cost,first_rest,expected_end,reason",
    [
        (1, 22, 24, "HORIZON"),
        (3, 19, 22, "HORIZON_ACTION"),
        (25, 0, 0, "HORIZON_ACTION"),
    ],
)
def test_move_horizon_uses_actual_route_cost(
    monkeypatch: pytest.MonkeyPatch,
    cost: int,
    first_rest: int,
    expected_end: int,
    reason: str,
) -> None:
    def altered_world() -> JsonObject:
        world = resource_world()
        for route in obj(obj(world["movement"])["routes"]).values():
            obj(route)["traversal_cost"] = cost
        return world

    monkeypatch.setattr("journeymap.scenarios.alderwick.resources.resource_world", altered_world)

    class NearHorizon:
        calls = 0

        def generate(self, request: ProviderRequest) -> RawModelResponse:
            self.calls += 1
            action = (
                {"action_type": "REST", "payload": {"duration": first_rest}}
                if first_rest and self.calls == 1
                else {
                    "action_type": "MOVE",
                    "payload": {"route_id": "west-gate-to-village-square"},
                }
            )
            return RawModelResponse(json.dumps(action))

    data = run_trial(LLMController(NearHorizon()), trial_id="variable-cost").data
    manifest = obj(data["manifest"])
    assert manifest["simulation_end"] == expected_end and manifest["stop_reason"] == reason
    assert manifest["replay_equal"] is True
    actions = cast(list[JsonObject], data["action_requests"])
    stranger_moves = [
        a for a in actions if a["actor_id"] == "stranger" and a["action_type"] == "MOVE"
    ]
    assert len(stranger_moves) == (1 if cost == 1 else 0)


def test_move_precheck_calls_handler_and_invalid_move_is_still_engine_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = ActionRequest(
        "a", "r", "stranger", "o", 0, "MOVE", 1, {"route_id": "west-gate-to-village-square"}
    )
    context = ValidationContext("r", 0, resource_world())

    def timing(
        self: MoveHandler, request: ActionRequest, context: ValidationContext
    ) -> ActionTiming:
        return ActionTiming(7)

    monkeypatch.setattr(MoveHandler, "prepare", timing)
    assert action_duration(request, context) == 7
    assert action_duration(replace(request, payload={"route_id": "unknown"}), context) == 0


def test_timing_precheck_defect_preserves_record_as_infrastructure_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(*args: object) -> int:
        raise RuntimeError("do not store this")

    monkeypatch.setattr("journeymap.experiments.alderwick.action_duration", broken)
    trial = run_trial(LLMController(ProtocolFakeProvider()), trial_id="timing-defect")
    manifest = obj(trial.data["manifest"])
    assert manifest["status"] == "TERMINATED" and manifest["stop_reason"] == "TIMING_PRECHECK_ERROR"
    assert obj(manifest["research_inclusion"])["status"] == "EXCLUDED"
    assert trial.data["decisions"] and "do not store this" not in trial.serialized


@pytest.mark.parametrize("corruption", ["artifact", "manifest", "inventory", "missing"])
def test_new_export_audit_detects_corruption_without_modifying_record(
    hardened_trial: Record,
    tmp_path: Path,
    corruption: str,
) -> None:
    directory = tmp_path / "trial"
    before = hardened_trial.serialized
    export_trial(hardened_trial, directory)
    assert audit_export(directory)["status"] == "INCLUDED"
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if corruption == "artifact":
        (directory / "action_traces.jsonl").write_bytes(b"{}\n")
    elif corruption == "manifest":
        manifest["parameters"]["max_output_tokens"] = 512
    elif corruption == "inventory":
        del manifest["files_sha256"]["decisions.jsonl"]
    else:
        (directory / "decisions.jsonl").unlink()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    assert audit_export(directory)["status"] == "EXCLUDED"
    assert hardened_trial.serialized == before
    with pytest.raises((ValueError, OSError)):
        replay_export(directory)


def test_new_and_historical_replay_are_provider_free_and_original_bytes_unchanged(
    hardened_trial: Record,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("replay must not instantiate/call a provider or controller")

    for cls in (LLMController, OpenAIProvider, ProtocolFakeProvider):
        monkeypatch.setattr(cls, "__init__", forbidden)
        monkeypatch.setattr(cls, "decide" if cls is LLMController else "generate", forbidden)
    new = tmp_path / "new"
    export_trial(hardened_trial, new)
    assert replay_export(new).final_simulation_time == 24
    assert audit_export(new)["status"] == "INCLUDED"
    # Always test the v1 reader, including a clean CI checkout with no private trials.
    # This is a synthetic compatibility fixture, never a migrated official artifact.
    legacy_data = hardened_trial.data
    legacy_manifest = obj(legacy_data["manifest"])
    legacy_manifest["schema_version"] = 1
    obj(legacy_manifest["engine_manifest"])["engine_version"] = "0.0.0"
    for key in (
        "research_inclusion",
        "record_sha256",
        "provider_identity",
        "python_runtime",
        "configuration_version",
        "observation_budget_bytes",
        "source_unchanged_during_trial",
    ):
        legacy_manifest.pop(key)
    legacy_manifest["code"] = {
        key: obj(legacy_manifest["code"])[key]
        for key in ("git_commit", "working_tree_dirty", "source_sha256")
    }
    legacy = tmp_path / "synthetic-v1"
    export_trial(Record.capture(legacy_data), legacy)
    legacy_before = {path.name: path.read_bytes() for path in legacy.iterdir()}
    assert replay_export(legacy) == replay_export(new)
    assert audit_export(legacy)["reasons"] == ["NEW_PROVENANCE_REQUIRED"]
    assert legacy_before == {path.name: path.read_bytes() for path in legacy.iterdir()}

    # Local-only historical evidence adds the immutable official golden check.
    # It is intentionally not uploaded to GitHub or required for the CI v1 test.
    old = ROOT / "trials" / "m8-first-official-sol"
    if not old.exists():
        return
    before = {path.name: path.read_bytes() for path in old.iterdir()}
    digest = sha256()
    for name, payload in sorted(before.items()):
        digest.update(name.encode() + b"\0" + payload)
    assert len(before) == 17
    assert digest.hexdigest() == "dd2c737ad4d6f0277f23bd8092114ceb4e5d98c8f3391b15c3f8eb433554edf0"
    historical = json.loads(before["manifest.json"])
    assert historical["engine_manifest"]["engine_version"] == "0.0.0"
    report = replay_export(old)
    assert report.final_simulation_time == 24
    assert (
        report.final_state_digest
        == "c8357de86dc6066843a24e047e241b824f565be2d12ba540317e46f639c07733"
    )
    assert audit_export(old)["reasons"] == ["NEW_PROVENANCE_REQUIRED"]
    assert before == {path.name: path.read_bytes() for path in old.iterdir()}


@pytest.mark.parametrize(
    "returned",
    [
        "other-provider-model",
        "alias-other-2026-09-14",
        "alias-2026-99-14",
        "alias-2026-09-14-extra",
        "alias-20260914",
    ],
)
def test_alias_policy_rejects_unrelated_or_malformed_identities(returned: str) -> None:
    identity = provider_identity(OpenAIProvider())
    metadata: JsonObject = {
        "provider": "openai",
        "adapter_version": OpenAIProvider.version,
        "model": returned,
    }
    assert not response_identity_matches(identity, "alias", metadata)
    assert not response_identity_matches(identity, "alias-2026-09-13", metadata)


def test_dated_response_is_submitted_and_separately_auditable_offline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fixture = ProtocolFakeProvider()

    def generate(self: OpenAIProvider, request: ProviderRequest) -> RawModelResponse:
        raw = fixture.generate(request)
        return RawModelResponse(
            raw.text,
            {
                "provider": "openai",
                "adapter_version": self.version,
                "model": request.model + "-2026-09-14",
            },
        )

    monkeypatch.setattr(OpenAIProvider, "generate", generate)
    data = run_trial(LLMController(OpenAIProvider(), model="test-alias"), trial_id="alias").data
    manifest = obj(data["manifest"])
    assert manifest["model"] == "test-alias"
    assert manifest["response_models"] == ["test-alias-2026-09-14"]
    assert len(cast(list[JsonValue], data["action_requests"])) == 22
    export_trial(Record.capture(data), tmp_path / "alias")
    assert audit_export(tmp_path / "alias")["status"] == "INCLUDED"


@pytest.mark.parametrize(
    "mutation,reason",
    [
        ("missing_result", "ACTION_TRACE_INCOMPLETE"),
        ("decision_trace", "ACTION_TRACE_INCOMPLETE"),
        ("observation_owner", "ACTION_TRACE_INCOMPLETE"),
        ("activation", "CONFIGURATION_INTEGRITY"),
        ("prompt", "CONFIGURATION_INTEGRITY"),
        ("missing_request", "CONFIGURATION_INTEGRITY"),
        ("bounds", "CONFIGURATION_INTEGRITY"),
        ("cycle", "CONFIGURATION_INTEGRITY"),
        ("response_models", "PROVIDER_IDENTITY_INCONSISTENCY"),
        ("replay", "REPLAY_MISMATCH"),
    ],
)
def test_independent_audit_rejects_resealed_included_records(
    hardened_trial: Record,
    tmp_path: Path,
    mutation: str,
    reason: str,
) -> None:
    data = hardened_trial.data
    manifest = obj(data["manifest"])
    first = obj(cast(list[JsonValue], data["decisions"])[0])
    if mutation == "missing_result":
        obj(cast(list[JsonValue], data["action_traces"])[0])["result"] = None
    elif mutation == "decision_trace":
        first["action_trace_sequences"] = []
        first["receipt"] = None
    elif mutation == "observation_owner":
        obj(cast(list[JsonValue], data["observations"])[0])["actor_id"] = "hugh"
    elif mutation == "activation":
        first["activation_sequence"] = 99
    elif mutation == "prompt":
        obj(first["provider_request"])["prompt"] = "different prompt"
    elif mutation == "missing_request":
        first["provider_request"] = None
    elif mutation == "bounds":
        manifest["termination_policy"] = {}
    elif mutation == "cycle":
        manifest["npc_activation_cycle"] = []
    elif mutation == "response_models":
        manifest["response_models"] = ["different-model"]
    else:
        # Integrity remains valid and the stored replay_equal/INCLUDED claims stay true.
        obj(data["engine_report"])["rng_draw_count"] = 99
    manifest["record_sha256"] = record_digest(data)
    export_trial(Record.capture(data), tmp_path / "tampered")
    result = audit_export(tmp_path / "tampered")
    assert result["status"] == "EXCLUDED"
    assert reason in cast(list[str], result["reasons"])
    assert result["stored_inclusion_matches"] is False
    assert "STORED_INCLUSION_MISMATCH" in cast(list[str], result["reasons"])
    assert obj(result["stored_inclusion"])["status"] == "INCLUDED"
    assert obj(result["recomputed_inclusion"])["status"] == "EXCLUDED"


def test_audit_detects_changed_stored_result_without_trusting_it(
    hardened_trial: Record,
    tmp_path: Path,
) -> None:
    directory = tmp_path / "trial"
    export_trial(hardened_trial, directory)
    path = directory / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["research_inclusion"] = {
        "policy_version": "pre-experiment-1",
        "status": "EXCLUDED",
        "reasons": ["invented"],
    }
    path.write_text(json.dumps(manifest), encoding="utf-8")
    before = path.read_bytes()
    audit = audit_export(directory)
    assert audit["reasons"] == ["STORED_INCLUSION_MISMATCH"]
    assert obj(audit["recomputed_inclusion"])["status"] == "INCLUDED"
    assert audit["stored_inclusion_matches"] is False
    assert path.read_bytes() == before


@pytest.mark.parametrize("artifact", ["engine_report.json", "action_traces.jsonl", "manifest.json"])
def test_missing_required_artifact_is_audit_failure(
    hardened_trial: Record,
    tmp_path: Path,
    artifact: str,
) -> None:
    export_trial(hardened_trial, tmp_path / "trial")
    (tmp_path / "trial" / artifact).unlink()
    assert audit_export(tmp_path / "trial")["status"] == "EXCLUDED"


def test_start_invalid_move_at_tick_23_reaches_engine_rejection() -> None:
    class InvalidMove:
        calls = 0

        def generate(self, request: ProviderRequest) -> RawModelResponse:
            self.calls += 1
            action = (
                {"action_type": "REST", "payload": {"duration": 21}}
                if self.calls == 1
                else {"action_type": "MOVE", "payload": {"route_id": "unknown"}}
            )
            return RawModelResponse(json.dumps(action))

    data = run_trial(LLMController(InvalidMove()), trial_id="invalid-move-at-end").data
    results = cast(list[JsonObject], data["action_results"])
    assert results[-1]["status"] == "REJECTED"
    assert results[-1]["started_at"] == results[-1]["resolved_at"] == 23
    assert obj(data["manifest"])["stop_reason"] == "CONSECUTIVE_FAILURES"
    assert obj(data["manifest"])["replay_equal"] is True


def test_meaningless_predicate_is_a_model_outcome_not_exclusion() -> None:
    class BadQuestion(ProtocolFakeProvider):
        def generate(self, request: ProviderRequest) -> RawModelResponse:
            raw = super().generate(request)
            action = json.loads(raw.text)
            if action["action_type"] == "ASK":
                action["payload"]["predicate"] = "nonexistent_predicate"
            if self.calls > 7:
                tick = json.loads(request.prompt.split("\nINPUT_JSON\n", 1)[1])["observation"][
                    "simulation_time"
                ]
                action = {"action_type": "REST", "payload": {"duration": 24 - tick}}
            return RawModelResponse(json.dumps(action), raw.metadata)

    trial = run_trial(LLMController(BadQuestion()), trial_id="bad-predicate")
    manifest = obj(trial.data["manifest"])
    assert manifest["status"] == "COMPLETED"
    assert obj(manifest["research_inclusion"])["status"] == "INCLUDED"


def test_replay_audit_checks_initial_inputs_even_with_resealed_manifest(
    hardened_trial: Record,
    tmp_path: Path,
) -> None:
    data = hardened_trial.data
    manifest = obj(data["manifest"])
    # A false start-time claim must fail even when the final report remains untouched.
    obj(manifest["engine_manifest"])["start_time"] = 1
    manifest["record_sha256"] = record_digest(data)
    export_trial(Record.capture(data), tmp_path / "initial-inputs")
    result = audit_export(tmp_path / "initial-inputs")
    assert result["status"] == "EXCLUDED"
    assert "REPLAY_MISMATCH" in cast(list[str], result["reasons"])
