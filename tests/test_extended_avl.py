"""
Pikina OS — Extended AVL Test Suite (Phase 4a)
Verifies validation rules, claimed vs actual level checks, Level 4+ confirmation gating,
retry safety limits, and EventBus logging.
"""
import sys
import os
import time
import ctypes
import sqlite3
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.eventbus.bus import EventBus
from core.eventbus.replay import ReplayStore
from core.registry.loader import CapabilityRegistry
from core.validation.failure_classes import FailureClass
from core.validation.schema_check import validate_model_action

# --- Setup Mocks ---
mock_mb_return = 6  # IDYES
message_box_calls = []

def mock_MessageBoxW(hwnd, text, caption, utype):
    message_box_calls.append({
        "text": text,
        "caption": caption,
        "utype": utype
    })
    return mock_mb_return

# Patch MessageBoxW
ctypes.windll.user32.MessageBoxW = mock_MessageBoxW

# Setup Registry with a mocked Level 4 tool
registry = CapabilityRegistry()
original_get_manifest = registry.get_manifest

def mock_get_manifest(tool_name):
    if tool_name == "system.destructive_test_action":
        return {
            "tool": "system.destructive_test_action",
            "description": "Test Level 4 high risk action",
            "permission_level": 4,
            "params_schema": {
                "type": "object",
                "required": ["path"],
                "properties": {
                    "path": {"type": "string"}
                }
            }
        }
    return original_get_manifest(tool_name)

registry.get_manifest = mock_get_manifest


def run_tests():
    print("=" * 65)
    print("  Pikina OS — Phase 4a Extended AVL Validation Test Suite")
    print("=" * 65)

    bus = EventBus()
    replay = ReplayStore()

    # Clear previous validation.rejected events from replay store to avoid cross-contamination
    with sqlite3.connect(replay.db_path) as conn:
        conn.execute("DELETE FROM events WHERE topic = 'validation.rejected'")
        conn.commit()

    # -------------------------------------------------------------
    # Case 1: Valid action, correct claimed_permission_level, level < 4
    # -------------------------------------------------------------
    print("\n[Case 1] Valid action, correct claimed_permission_level...")
    action1 = {
        "tool": "app.open",
        "params": {"path": "notepad"},
        "claimed_permission_level": 1,
        "provenance": "model_output"
    }
    is_valid, fc, reason = validate_model_action(action1, registry)
    assert is_valid, f"Failed: Expected valid action, got error: {reason}"
    assert fc is None and reason is None
    print("  [PASS] Valid action approved.")

    # -------------------------------------------------------------
    # Case 2: claimed_permission_level doesn't match manifest level
    # -------------------------------------------------------------
    print("\n[Case 2] claimed_permission_level mismatch...")
    action2 = {
        "tool": "app.open",
        "params": {"path": "notepad"},
        "claimed_permission_level": 5,  # actual level is 1
        "provenance": "model_output"
    }
    is_valid, fc, reason = validate_model_action(action2, registry)
    assert not is_valid
    assert fc == FailureClass.VALIDATION
    assert "model_claimed_level_5_actual_1" in reason
    
    # Verify EventBus log
    events = replay.query(since_minutes=1, topic="validation.rejected")
    assert len(events) > 0
    assert events[-1]["failure_class"] == FailureClass.VALIDATION
    assert events[-1]["payload"]["outcome"] == "rejected"
    assert "model_claimed_level_5_actual_1" in events[-1]["payload"]["reason"]
    print("  [PASS] Claimed level mismatch rejected and logged.")

    # -------------------------------------------------------------
    # Case 3: Unknown tool name
    # -------------------------------------------------------------
    print("\n[Case 3] Unknown tool name...")
    action3 = {
        "tool": "system.nonexistent_tool",
        "params": {},
        "claimed_permission_level": 0,
        "provenance": "model_output"
    }
    is_valid, fc, reason = validate_model_action(action3, registry)
    assert not is_valid
    assert fc == FailureClass.VALIDATION
    assert reason == "unknown_tool"

    # Verify EventBus log
    events = replay.query(since_minutes=1, topic="validation.rejected")
    assert events[-1]["failure_class"] == FailureClass.VALIDATION
    assert events[-1]["payload"]["reason"] == "unknown_tool"
    print("  [PASS] Unknown tool rejected and logged.")

    # -------------------------------------------------------------
    # Case 4: Correct claim, but actual >= 4 (Hold for Confirmation)
    # -------------------------------------------------------------
    print("\n[Case 4] Level 4+ hold for confirmation...")
    action4 = {
        "tool": "system.destructive_test_action",
        "params": {"path": "C:\\Windows\\System32"},
        "claimed_permission_level": 4,
        "provenance": "model_output"
    }

    # Case 4a: User APPROVES dialog
    global mock_mb_return
    mock_mb_return = 6  # IDYES
    message_box_calls.clear()

    print("  Testing Dialog Approved branch...")
    is_valid, fc, reason = validate_model_action(action4, registry)
    assert is_valid
    assert fc is None and reason is None
    assert len(message_box_calls) == 1
    assert "Test Level 4 high risk action" in message_box_calls[0]["text"]

    # Verify EventBus logs (should contain a "held" event then an "approved" event)
    events = replay.query(since_minutes=1, topic="validation.rejected")
    # Verify the last event shows outcome approved
    assert events[-1]["payload"]["outcome"] == "approved"
    # Verify the event before the last shows outcome held
    assert events[-2]["payload"]["outcome"] == "held"
    assert events[-2]["failure_class"] == FailureClass.PERMISSION
    print("    [PASS] Approved path blocks, displays MessageBoxW, and logs held -> approved.")

    # Case 4b: User DENIES dialog
    mock_mb_return = 7  # IDNO
    message_box_calls.clear()

    print("  Testing Dialog Denied branch...")
    is_valid, fc, reason = validate_model_action(action4, registry)
    assert not is_valid
    assert fc == FailureClass.PERMISSION
    assert reason == "User declined consent dialog."
    assert len(message_box_calls) == 1

    # Verify EventBus logs (should contain a "held" event then a "denied" event)
    events = replay.query(since_minutes=1, topic="validation.rejected")
    assert events[-1]["payload"]["outcome"] == "denied"
    assert events[-1]["failure_class"] == FailureClass.PERMISSION
    assert events[-2]["payload"]["outcome"] == "held"
    print("    [PASS] Denied path blocks, displays MessageBoxW, and logs held -> denied.")

    # -------------------------------------------------------------
    # Case 5: Retry safety limit (3rd failure returns FailureClass.BUG)
    # -------------------------------------------------------------
    print("\n[Case 5] Retry safety limit (bug classification)...")
    action5 = {
        "tool": "app.open",
        "params": {},  # missing 'path'
        "claimed_permission_level": 1,
        "provenance": "model_output"
    }

    # 1st try: normal validation failure
    is_valid, fc, reason = validate_model_action(action5, registry, retries_so_far=0)
    assert not is_valid
    assert fc == FailureClass.VALIDATION

    # 2nd try: normal validation failure
    is_valid, fc, reason = validate_model_action(action5, registry, retries_so_far=1)
    assert not is_valid
    assert fc == FailureClass.VALIDATION

    # 3rd try: bug classification failure
    is_valid, fc, reason = validate_model_action(action5, registry, retries_so_far=2)
    assert not is_valid
    assert fc == FailureClass.BUG
    assert reason == "repeated_validation_failure_same_task"

    # Verify EventBus log
    events = replay.query(since_minutes=1, topic="validation.rejected")
    assert events[-1]["failure_class"] == FailureClass.BUG
    assert events[-1]["payload"]["reason"] == "repeated_validation_failure_same_task"
    print("  [PASS] 3rd failure classified as BUG and logged.")

    # -------------------------------------------------------------
    # Case 6: claimed_permission_level missing entirely
    # -------------------------------------------------------------
    print("\n[Case 6] Missing claimed_permission_level...")
    action6 = {
        "tool": "app.open",
        "params": {"path": "notepad"},
        "provenance": "model_output"
    }
    is_valid, fc, reason = validate_model_action(action6, registry)
    assert not is_valid
    assert fc == FailureClass.VALIDATION
    assert reason == "model_did_not_declare_permission_level"

    # Verify EventBus log
    events = replay.query(since_minutes=1, topic="validation.rejected")
    assert events[-1]["failure_class"] == FailureClass.VALIDATION
    assert events[-1]["payload"]["reason"] == "model_did_not_declare_permission_level"
    print("  [PASS] Missing claimed level rejected and logged.")

    print("\n" + "=" * 65)
    print("  === ALL PHASE 4a EXTENDED AVL TESTS PASSED ===")
    print("=" * 65 + "\n")


if __name__ == "__main__":
    run_tests()
