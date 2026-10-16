"""
Shared pytest fixtures.

Every test runs in its own temporary working directory, so the CWD-relative
stores (data/pikina.db, data/vector_memory.db, ...) and the replay log never
touch your real Pikina data. Consent dialogs and the embedding model are faked.
"""
import os
import sys
import hashlib
import tempfile
from pathlib import Path

import numpy as np
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Modules that create stores at import time (backend_server) must not touch real
# data either, so the whole session starts inside a scratch directory.
_SESSION_DIR = Path(tempfile.mkdtemp(prefix="pikina-tests-"))
(_SESSION_DIR / "data").mkdir()
os.environ["PIKINA_API_TOKEN_FILE"] = str(_SESSION_DIR / "data" / ".api_token")


def pytest_sessionstart(session):
    # After pytest has resolved its paths, before any test module is imported.
    os.chdir(_SESSION_DIR)

from core.eventbus.bus import EventBus  # noqa: E402
from core.eventbus.replay import ReplayStore  # noqa: E402
from core.mcm import killswitch  # noqa: E402
from core.memory.vector_store import VectorStore  # noqa: E402

ReplayStore.__init__.__defaults__ = (_SESSION_DIR / "replay_store.sqlite",)


class FakeEmbedder:
    """Deterministic bag-of-words embedding: identical text -> similarity 1.0, unrelated -> ~0."""
    dim = 384

    def encode(self, text: str):
        vec = np.zeros(self.dim, dtype=np.float32)
        for word in text.lower().split():
            h = int(hashlib.md5(word.encode()).hexdigest(), 16)
            vec[h % self.dim] += 1.0
        if not vec.any():
            vec[0] = 1.0
        return vec / np.linalg.norm(vec)


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    (tmp_path / "data").mkdir()
    monkeypatch.chdir(tmp_path)

    # Fresh replay log + event bus per test
    monkeypatch.setattr(ReplayStore.__init__, "__defaults__", (tmp_path / "replay_store.sqlite",))
    monkeypatch.setattr(EventBus, "_instance", None)

    # Fresh vector store with the fake embedder
    monkeypatch.setattr(VectorStore, "_instance", None)
    monkeypatch.setattr(VectorStore, "model", property(lambda self: FakeEmbedder()))

    # User-editable config files live inside the repo — redirect them too
    monkeypatch.setattr("core.router.alias_resolver.ALIASES_FILE", tmp_path / "aliases.json")
    monkeypatch.setattr("core.preferences.profile_manager.PROFILE_FILE", tmp_path / "profile.json")

    killswitch.release()
    yield tmp_path
    killswitch.release()


class ConsentRecorder:
    """Stands in for the native dialog. Set .answer to control the user's click."""

    def __init__(self):
        self.answer = False
        self.calls = []

    def __call__(self, message: str) -> bool:
        self.calls.append(message)
        return self.answer


@pytest.fixture(autouse=True)
def consent(monkeypatch):
    recorder = ConsentRecorder()
    monkeypatch.setattr("core.osbridge.consent.ask", recorder)
    return recorder


@pytest.fixture
def registry():
    from core.registry.loader import CapabilityRegistry
    return CapabilityRegistry()


@pytest.fixture
def replay():
    return ReplayStore()
