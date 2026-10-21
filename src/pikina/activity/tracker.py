"""
The opt-in window tracker: records which app has focus, app names only.

A focus source per OS (pikina.osbridge.focus) calls _on_focus on its own thread;
this daemon cleans the stream on a second thread and writes app_switches rows:

- Pikina's own windows are ignored (typing a command doesn't count as a switch,
  and never becomes the "active app" of that command).
- Password managers, unlock prompts and detectable private windows are written as
  "excluded"; nothing focused is written as "none".
- A focus shorter than MIN_DWELL seconds is dropped (focus-follows-mouse and
  alt-tab flicker), and repeats of the same app are collapsed.
- While the activity log is paused, nothing is written and no active app is reported.

Focus events never go through the event bus, so they never reach the replay log.
"""
import os
import queue
import threading
import time
from datetime import datetime, timezone

from pikina.activity import privacy, recorder
from pikina.activity.store import ActivityStore
from pikina.osbridge.apps import canonical_app
from pikina.activity.keys import _token

MIN_DWELL = 1.0
_STOP = object()


class WindowTracker:
    def __init__(self, *, detect=None, make_source=None, store_factory=ActivityStore,
                 min_dwell: float = MIN_DWELL, clock=time.monotonic, is_paused=None):
        if detect is None or make_source is None:
            from pikina.osbridge import focus
            detect = detect or focus.detect_backend
            make_source = make_source or focus.make_source
        self._detect, self._make_source = detect, make_source
        self._store_factory = store_factory
        self._min_dwell, self._clock = min_dwell, clock
        self._is_paused = is_paused or (lambda: not recorder.is_enabled())
        self._queue: queue.Queue = queue.Queue(maxsize=1000)
        self._own_pids = {os.getpid(), os.getppid()}
        self._lock = threading.Lock()
        self._current: str | None = None  # last real app written (not excluded/none)
        self._source = None
        self._writer = None
        self.backend: str | None = None
        self.reason: str | None = None
        self.dropped = 0

    # -- lifecycle -----------------------------------------------------------
    def start(self) -> bool:
        backend, reason = self._detect()
        self.backend, self.reason = backend, reason
        if not backend:
            return False
        self._writer = threading.Thread(target=self._write_loop, name="WindowTrackerWriter", daemon=True)
        self._writer.start()
        try:
            self._source = self._make_source(backend, self._on_focus)
            self._source.start()
        except Exception as exc:
            self.reason = f"Couldn't start the {backend} listener ({type(exc).__name__})."
            self.stop()
            return False
        recorder.set_active_app_provider(self.current_app)
        return True

    def stop(self) -> None:
        recorder.set_active_app_provider(None)
        src, self._source = self._source, None
        if src is not None:
            try:
                src.stop()
                src.join(timeout=3)
            except Exception:
                pass
        writer, self._writer = self._writer, None
        if writer is not None:
            try:
                self._queue.put_nowait(_STOP)
            except queue.Full:
                pass
            writer.join(timeout=3)

    @property
    def running(self) -> bool:
        return bool(self._writer and self._writer.is_alive() and self._source and self._source.is_alive())

    def status(self) -> dict:
        return {"running": self.running, "backend": self.backend,
                "reason": None if self.running else self.reason}

    def current_app(self) -> str | None:
        """The app you're in (never Pikina), for the active_app of a command; None when paused."""
        if self._is_paused():
            return None
        with self._lock:
            return self._current

    # -- source thread ---------------------------------------------------------
    def _on_focus(self, app, private, pid) -> None:
        """Called by the focus source. Must be cheap: classify and hand over."""
        try:
            self._queue.put_nowait((datetime.now(timezone.utc), self._clock(), app, bool(private), pid))
        except queue.Full:
            self.dropped += 1

    # -- writer thread ---------------------------------------------------------
    def _label(self, app, private, pid) -> str | None:
        """None = ignore this event (Pikina's own window)."""
        canon = canonical_app(app) if app else None
        if (pid is not None and pid in self._own_pids) or privacy.is_pikina(app, canon):
            return None
        if not app:
            return privacy.NOTHING
        if private or privacy.is_excluded(app, canon):
            return privacy.EXCLUDED
        return _token(canon) if canon else "?"

    def _commit(self, ts, label) -> None:
        try:
            store = self._store_factory()
            store.add_switch(ts=ts, app=label, backend=self.backend)
            recorder.maybe_prune(store)
        except Exception as exc:
            print(f"[Tracker] Not recorded: {type(exc).__name__}")
        with self._lock:
            self._current = None if label in (privacy.EXCLUDED, privacy.NOTHING) else label

    def _write_loop(self) -> None:
        pending = None   # (ts, mono, label) waiting to outlast MIN_DWELL
        last = None      # last label written
        while True:
            try:
                item = self._queue.get(timeout=0.2 if pending else 1.0)
            except queue.Empty:
                item = None
            if item is _STOP:
                return
            if self._is_paused():
                pending, last = None, None
                with self._lock:
                    self._current = None
                continue
            if item is None:  # quiet: write the pending focus once it has lasted long enough
                if pending and self._clock() - pending[1] >= self._min_dwell:
                    self._commit(pending[0], pending[2])
                    last, pending = pending[2], None
                continue
            ts, mono, app, private, pid = item
            label = self._label(app, private, pid)
            if label is None:
                continue
            if pending:
                if label == pending[2]:
                    continue
                # Judge by when the next focus arrived, not by when we got round to it.
                if mono - pending[1] >= self._min_dwell:
                    self._commit(pending[0], pending[2])
                    last = pending[2]
                pending = None  # otherwise it was flicker: drop the short-lived focus
            if label == last:
                continue
            pending = (ts, mono, label)
