"""
Global kill switch. When engaged, no capability executes from any path
(Tier 1, Tier 2, alias chains, direct API) until it is released.
"""
import threading

_engaged = threading.Event()


def engage() -> None:
    _engaged.set()


def release() -> None:
    _engaged.clear()


def is_engaged() -> bool:
    return _engaged.is_set()
