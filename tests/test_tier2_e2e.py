"""
Pikina OS — Tier 2 Ollama Integration Test Suite (Phase 4e)
Verifies prompt structures, JSON parsing, retry routines, timeout safety limits, and latency logging.
"""
import sys
import os
import json
import time
import sqlite3
import requests
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.eventbus.bus import EventBus
from core.eventbus.replay import ReplayStore
from core.validation.failure_classes import FailureClass
from core.router.tier2_ollama import Tier2Router
from core.registry.loader import CapabilityRegistry


def run_tests():
    print("=" * 65)
    print("  Pikina OS — Phase 4e Tier 2 Ollama Integration Test Suite")
    print("=" * 65)

    bus = EventBus()
    replay = ReplayStore()
    registry = CapabilityRegistry()

    # Clear previous latency and retry events from replay store
    with sqlite3.connect(replay.db_path) as conn:
        conn.execute("DELETE FROM events WHERE topic IN ('model.latency', 'router.tier2_retry', 'router.tier2_error')")
        conn.commit()

    # Save original post
    original_post = requests.post

    # -------------------------------------------------------------
    # Case 1: Clean, correct JSON response
    # -------------------------------------------------------------
    print("\n[Case 1] Correct JSON response on first try...")
    router = Tier2Router(registry=registry)

    def mock_clean_post(url, json=None, timeout=None):
        class MockResponse:
            status_code = 200
            def json(self):
                return {"response": '{"tool": "app.open", "params": {"path": "notepad"}, "claimed_permission_level": 1, "provenance": "model_output"}'}
        return MockResponse()

    requests.post = mock_clean_post

    result = router.route("open notepad please")
    assert result.get("status") == "ok"
    
    # Verify latency event was logged
    events = replay.query(since_minutes=1, topic="model.latency")
    assert len(events) > 0
    assert "latency_ms" in events[-1]["payload"]
    print("  [PASS] Correct JSON handled, latency logged.")

    # -------------------------------------------------------------
    # Case 2: Markdown wrapped JSON
    # -------------------------------------------------------------
    print("\n[Case 2] Markdown code fenced JSON response...")
    
    def mock_markdown_post(url, json=None, timeout=None):
        class MockResponse:
            status_code = 200
            def json(self):
                # Returns JSON wrapped in markdown fences
                return {"response": '```json\n{"tool": "app.open", "params": {"path": "calc"}, "claimed_permission_level": 1, "provenance": "model_output"}\n```'}
        return MockResponse()

    requests.post = mock_markdown_post

    result = router.route("open calculator")
    assert result.get("status") == "ok"
    print("  [PASS] Markdown fences cleaned and parsed successfully.")

    # -------------------------------------------------------------
    # Case 3: Invalid JSON on first attempt -> Stricter Retry succeeds
    # -------------------------------------------------------------
    print("\n[Case 3] Invalid JSON first -> Retry prompt succeeds...")
    
    calls = []
    def mock_retry_post(url, json=None, timeout=None):
        calls.append(json["prompt"])
        class MockResponse:
            status_code = 200
            def json(self):
                if len(calls) == 1:
                    # Return invalid JSON
                    return {"response": "Sure, I can help! Here is the action: {invalid json}"}
                else:
                    # Return valid JSON on second retry
                    return {"response": '{"tool": "app.open", "params": {"path": "paint"}, "claimed_permission_level": 1, "provenance": "model_output"}'}
        return MockResponse()

    requests.post = mock_retry_post

    result = router.route("run paint")
    assert result.get("status") == "ok"
    assert len(calls) == 2
    assert "Your previous response was not valid JSON" in calls[1]
    
    # Verify retry event logged to EventBus
    retries = replay.query(since_minutes=1, topic="router.tier2_retry")
    assert len(retries) > 0
    assert "invalid json" in retries[-1]["payload"]["raw_response"]
    print("  [PASS] Invalid JSON triggered retry prompt and parsed successfully on second try.")

    # -------------------------------------------------------------
    # Case 4: Timeout safety limit
    # -------------------------------------------------------------
    print("\n[Case 4] Ollama HTTP call times out (15s limits)...")
    
    def mock_timeout_post(url, json=None, timeout=None):
        raise requests.Timeout("Connection timed out")

    requests.post = mock_timeout_post

    result = router.route("open notepad")
    assert result.get("status") == "error"
    assert result.get("reason") == "timeout"
    assert result.get("failure_class") == FailureClass.INFRASTRUCTURE
    
    # Verify error logged to EventBus
    errors = replay.query(since_minutes=1, topic="router.tier2_error")
    assert len(errors) > 0
    assert errors[-1]["failure_class"] == FailureClass.INFRASTRUCTURE
    print("  [PASS] Timeout returned infrastructure failure and logged to EventBus.")

    # Restore requests
    requests.post = original_post

    print("\n" + "=" * 65)
    print("  === ALL PHASE 4e TIER 2 INTEGRATION TESTS PASSED ===")
    print("=" * 65 + "\n")


if __name__ == "__main__":
    run_tests()
