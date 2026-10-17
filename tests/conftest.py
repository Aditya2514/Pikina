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

# The whole session (including anything created at import time) writes into a
# scratch data folder, never into your real Pikina data.
_SESSION_DIR = Path(tempfile.mkdtemp(prefix="pikina-tests-"))
os.environ["PIKINA_DATA_DIR"] = str(_SESSION_DIR)
os.environ.pop("PIKINA_API_TOKEN_FILE", None)

from pikina.eventbus.bus import EventBus  # noqa: E402
from pikina.mcm import killswitch  # noqa: E402
from pikina.memory.vector_store import VectorStore  # noqa: E402


def pytest_sessionstart(session):
    # Some code still resolves relative paths against the working directory
    os.chdir(_SESSION_DIR)


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
    """Each test gets an empty data folder, fresh singletons and the fake embedder."""
    monkeypatch.setenv("PIKINA_DATA_DIR", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(EventBus, "_instance", None)
    monkeypatch.setattr(VectorStore, "_instance", None)
    monkeypatch.setattr(VectorStore, "model", property(lambda self: FakeEmbedder()))
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
    monkeypatch.setattr("pikina.osbridge.consent.ask", recorder)
    return recorder


@pytest.fixture
def registry():
    from pikina.registry.loader import CapabilityRegistry
    return CapabilityRegistry()


@pytest.fixture
def replay():
    from pikina.eventbus.replay import ReplayStore
    return ReplayStore()
