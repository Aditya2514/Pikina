"""
Phase 4e — Tier 2 (Ollama) router: JSON parsing, retry, timeouts, offline
fallback, latency logging, relevance filter and the shared consent gate.
No real Ollama is needed: requests.post is replaced per test.
"""
import pytest
import requests

from core.validation.failure_classes import FailureClass
from core.router import tier2_ollama
from core.router.tier2_ollama import Tier2Router, select_relevant_tools


class _Resp:
    def __init__(self, text, status=200):
        self.status_code = status
        self._text = text

    def json(self):
        return {"response": self._text}


@pytest.fixture
def executed(registry, monkeypatch):
    """Record capability executions instead of launching real apps."""
    calls = []

    def fake_execute(tool, params):
        calls.append((tool, params))
        return {"status": "ok", "tool": tool}

    monkeypatch.setattr(registry, "execute", fake_execute)
    return calls


@pytest.fixture
def router(registry):
    return Tier2Router(registry=registry)


def _reply(monkeypatch, *texts):
    prompts = []

    def fake_post(url, json=None, timeout=None):
        prompts.append(json)
        return _Resp(texts[min(len(prompts), len(texts)) - 1])

    monkeypatch.setattr(requests, "post", fake_post)
    return prompts


APP_OPEN = '{"tool": "app.open", "params": {"path": "notepad"}, "claimed_permission_level": 1, "provenance": "model_output"}'


def test_clean_json_executes_and_logs_latency(router, executed, monkeypatch, replay):
    prompts = _reply(monkeypatch, APP_OPEN)
    assert router.route("open notepad please")["status"] == "ok"
    assert executed == [("app.open", {"path": "notepad"})]
    assert prompts[0]["format"] == "json"
    assert prompts[0]["keep_alive"]
    assert "latency_ms" in replay.query(since_minutes=1, topic="model.latency")[-1]["payload"]


def test_markdown_fenced_json_is_cleaned(router, executed, monkeypatch):
    _reply(monkeypatch, "```json\n" + APP_OPEN + "\n```")
    assert router.route("open calculator")["status"] == "ok"


def test_invalid_json_triggers_one_stricter_retry(router, executed, monkeypatch, replay):
    prompts = _reply(monkeypatch, "Sure! {invalid json}", APP_OPEN)
    assert router.route("run paint")["status"] == "ok"
    assert len(prompts) == 2
    assert "Your previous response was not valid JSON" in prompts[1]["prompt"]
    assert "invalid json" in replay.query(since_minutes=1, topic="router.tier2_retry")[-1]["payload"]["raw_response"]


def test_timeout_is_infrastructure_failure(router, monkeypatch, replay):
    def slow(url, json=None, timeout=None):
        raise requests.Timeout("timed out")
    monkeypatch.setattr(requests, "post", slow)

    result = router.route("open notepad")
    assert result["status"] == "error" and result["reason"] == "timeout"
    assert result["failure_class"] == FailureClass.INFRASTRUCTURE
    assert replay.query(since_minutes=1, topic="router.tier2_error")[-1]["failure_class"] == FailureClass.INFRASTRUCTURE


def test_ollama_offline_degrades_to_no_match(router, monkeypatch):
    def refused(url, json=None, timeout=None):
        raise requests.ConnectionError("refused")
    monkeypatch.setattr(requests, "post", refused)

    result = router.route("launch something to write text")
    assert result["status"] == "no_match"
    assert result["reason"] == "tier2_unavailable"


def test_model_no_match_is_reported(router, monkeypatch):
    _reply(monkeypatch, '{"tool": "no_match", "params": {}, "claimed_permission_level": 0}')
    assert router.route("tell me a joke")["status"] == "no_match"


def test_non_object_json_is_rejected(router, monkeypatch):
    _reply(monkeypatch, '["app.open"]', '["app.open"]')
    assert router.route("open it")["reason"] == "json_parse_failed"


def test_timeout_and_model_are_configurable(registry):
    r = Tier2Router(registry=registry, model_name="qwen2.5:3b", timeout=7, base_url="http://box:11434/")
    assert (r.model_name, r.timeout, r.url) == ("qwen2.5:3b", 7, "http://box:11434/api/generate")


def test_kill_switch_blocks_model_actions(router, executed, monkeypatch):
    from core.mcm import killswitch
    _reply(monkeypatch, APP_OPEN)
    killswitch.engage()
    assert router.route("open notepad")["status"] == "denied"
    assert executed == []


def test_relevance_filter_only_kicks_in_for_large_registries(registry):
    tools = registry.list_tools()
    assert select_relevant_tools("anything", tools, limit=len(tools)) == tools

    picked = select_relevant_tools("can you launch an application to write text", tools, limit=3)
    assert "app.open" in [t["tool"] for t in picked]
    picked = select_relevant_tools("mark the task as complete", tools, limit=4)
    assert "todo.complete" in [t["tool"] for t in picked]


def test_prompt_respects_tool_limit(router, executed, monkeypatch):
    monkeypatch.setattr(tier2_ollama, "MAX_PROMPT_TOOLS", 3)
    prompts = _reply(monkeypatch, APP_OPEN)
    router.route("launch an application")
    listing = prompts[0]["prompt"].split("Available capabilities:")[1].split("Memory context:")[0]
    assert listing.count("[permission_level=") == 3
