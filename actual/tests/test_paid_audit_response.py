"""Journal-to-call response retention and owner disposition regressions."""

import copy
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from test_paid_audit import child_replay, make_report
from test_paid_life import offline as offline

from journeymap.adapters.life_http import LifeHTTPProvider
from journeymap.experiments.paid_replay import replay_paid_report


def remove_response(report: dict[str, Any], case: str) -> None:
    call = report["controller"]["calls"][0]
    assert call["raw"] is not None
    call["raw"] = None
    if case == "ready-pending":
        call["status"] = "PENDING"
    elif case == "ready-error":
        call.update(status="ERROR", error="UNRECORDED_ERROR")


@pytest.mark.parametrize("case", ["timeout", "late", "ready-pending", "ready-error"])
def test_owner_received_response_cannot_disappear_or_change_state(
    tmp_path: Path, case: str
) -> None:
    kind = "ready" if case.startswith("ready-") else case
    original = make_report(tmp_path / "registry", kind)
    report = copy.deepcopy(original)
    remove_response(report, case)
    # These are call-only contradictions, without resealing or journal changes.
    assert report["controller"]["journal"] == original["controller"]["journal"]
    assert report["controller"]["audit_snapshot"] == original["controller"]["audit_snapshot"]
    assert report["controller"]["usage"] == original["controller"]["usage"]
    assert report["execution"] == original["execution"]
    with (
        patch.object(LifeHTTPProvider, "generate", side_effect=AssertionError("no Provider")),
        pytest.raises(ValueError),
    ):
        replay_paid_report(report)
    child = child_replay(report, tmp_path / "bad.json")
    assert child.returncode != 0 and '"audit_verified": true' not in child.stdout


def test_successful_delivery_without_owner_collection_has_no_call_raw(tmp_path: Path) -> None:
    report = make_report(tmp_path / "registry", "uncollected")
    call = report["controller"]["calls"][0]
    assert call["raw"] is None and call["status"] == "PENDING"
    stages = {row["stage"] for row in report["controller"]["journal"]}
    assert "RESPONSE_DELIVERY" in stages and "OWNER_PACKET" not in stages
    with patch.object(LifeHTTPProvider, "generate", side_effect=AssertionError("no Provider")):
        result = replay_paid_report(report)
    assert result["audit_verified"] and not result["outcome_certain"]
    child = child_replay(report, tmp_path / "valid.json")
    assert child.returncode == 0, child.stderr
    assert json.loads(child.stdout) == result


@pytest.mark.parametrize("kind", ["refusal", "incomplete"])
def test_classified_error_state_is_derived_even_when_raw_is_present(
    tmp_path: Path, kind: str
) -> None:
    report = make_report(tmp_path / "registry", kind)
    call = report["controller"]["calls"][0]
    assert call["raw"] is not None and call["status"] == "ERROR"
    call["status"] = "PENDING"
    with (
        patch.object(LifeHTTPProvider, "generate", side_effect=AssertionError("no Provider")),
        pytest.raises(ValueError, match="classified error disposition"),
    ):
        replay_paid_report(report)
    child = child_replay(report, tmp_path / "bad-state.json")
    assert child.returncode != 0 and '"audit_verified": true' not in child.stdout
