"""
Phase 4a — Extended Action Validation Layer for model-proposed actions:
claimed vs actual permission levels, unknown tools, high-risk holds,
retry limits and replay-log auditing.
"""
import pytest

from core.validation.failure_classes import FailureClass
from core.validation.schema_check import validate_model_action

DESTRUCTIVE = {
    "tool": "system.destructive_test_action",
    "description": "Test Level 4 high risk action",
    "permission_level": 4,
    "supports_rollback": False,
    "params_schema": {"type": "object", "required": ["path"], "properties": {"path": {"type": "string"}}},
}


@pytest.fixture
def avl_registry(registry, monkeypatch):
    real = registry.get_manifest
    monkeypatch.setattr(registry, "get_manifest",
                        lambda tool: DESTRUCTIVE if tool == DESTRUCTIVE["tool"] else real(tool))
    return registry


def _action(tool="app.open", params=None, level=1, **extra):
    a = {"tool": tool, "params": {"path": "notepad"} if params is None else params,
         "claimed_permission_level": level, "provenance": "model_output"}
    a.update(extra)
    return a


def _last_rejection(replay, n=1):
    return replay.query(since_minutes=1, topic="validation.rejected")[-n]


def test_valid_action_passes(avl_registry):
    assert validate_model_action(_action(), avl_registry) == (True, None, None)


def test_claimed_level_mismatch_rejected_and_logged(avl_registry, replay):
    ok, fc, reason = validate_model_action(_action(level=5), avl_registry)
    assert not ok and fc == FailureClass.VALIDATION
    assert "model_claimed_level_5_actual_1" in reason
    ev = _last_rejection(replay)
    assert ev["payload"]["outcome"] == "rejected"
    assert ev["failure_class"] == FailureClass.VALIDATION


def test_unknown_tool_rejected(avl_registry, replay):
    ok, fc, reason = validate_model_action(_action(tool="system.nonexistent", params={}, level=0), avl_registry)
    assert (ok, fc, reason) == (False, FailureClass.VALIDATION, "unknown_tool")
    assert _last_rejection(replay)["payload"]["reason"] == "unknown_tool"


def test_high_risk_action_held_then_approved(avl_registry, replay, consent):
    consent.answer = True
    action = _action(tool=DESTRUCTIVE["tool"], params={"path": "/etc"}, level=4)
    assert validate_model_action(action, avl_registry) == (True, None, None)
    assert len(consent.calls) == 1
    assert "Test Level 4 high risk action" in consent.calls[0]
    assert _last_rejection(replay, 1)["payload"]["outcome"] == "approved"
    held = _last_rejection(replay, 2)
    assert held["payload"]["outcome"] == "held" and held["failure_class"] == FailureClass.PERMISSION


def test_high_risk_action_held_then_denied(avl_registry, replay, consent):
    consent.answer = False
    action = _action(tool=DESTRUCTIVE["tool"], params={"path": "/etc"}, level=4)
    assert validate_model_action(action, avl_registry) == (
        False, FailureClass.PERMISSION, "User declined consent dialog.")
    assert len(consent.calls) == 1
    assert _last_rejection(replay, 1)["payload"]["outcome"] == "denied"
    assert _last_rejection(replay, 2)["payload"]["outcome"] == "held"


def test_third_failure_is_classified_as_bug(avl_registry, replay):
    bad = _action(params={})  # missing required 'path'
    assert validate_model_action(bad, avl_registry, retries_so_far=0)[1] == FailureClass.VALIDATION
    assert validate_model_action(bad, avl_registry, retries_so_far=1)[1] == FailureClass.VALIDATION
    ok, fc, reason = validate_model_action(bad, avl_registry, retries_so_far=2)
    assert (ok, fc, reason) == (False, FailureClass.BUG, "repeated_validation_failure_same_task")
    assert _last_rejection(replay)["failure_class"] == FailureClass.BUG


def test_missing_claimed_level_rejected(avl_registry):
    action = _action()
    del action["claimed_permission_level"]
    ok, fc, reason = validate_model_action(action, avl_registry)
    assert (ok, fc, reason) == (False, FailureClass.VALIDATION, "model_did_not_declare_permission_level")
