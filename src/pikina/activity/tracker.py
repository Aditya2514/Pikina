"""
The opt-in window tracker: records which app has focus, app names only.

A focus source per OS (pikina.osbridge.focus) calls _on_focus on its own thread;
this daemon cleans the stream on a second thread and writes app_switches rows:

- Pikina's own windows are ignored (typing a command doesn't count as a switch,
  and never becomes the "active app" of that command).
- Password managers, unlock prompts, your exclusions and detectable private windows
  are written as "excluded"; nothing focused is written as "none". A site opened as
  a Chromium app window counts as the browser, never as the site.
- A focus shorter than MIN_DWELL seconds is dropped (focus-follows-mouse and
  alt-tab flicker), and repeats of the same app are collapsed.
- Pausing writes one "paused" marker (so that time isn't credited to the last app)
  and nothing else until you resume; on resume the current app is picked up again.
  The tracker obeys pause only: "log commands" is a separate switch.

Focus events never go through the event bus, so they never reach the replay log.
"""
import os
import queue
import threading
import time
from datetime import datetime, timezone

from pikina.activity import privacy, recorder
from pikina.activity.keys import _token
from pikina.activity.store import ActivityStore
from pikina.osbridge.apps import canonical_app

MIN_DWELL = 1.0
WRITE_TIMEOUT = 1.0   # seconds to wait for activity.db before skipping a row
_STOP = object()


class WindowTracker:
    def __init__(self, *, detect=None, make_source=None, store_factory=None,
                 min_dwell: float = MIN_DWELL, clock=time.monotonic, is_paused=None):
        if detect is None or make_source is None:
            from pikina.osbridge import focus
            detect = detect or focus.detect_backend
            make_source = make_source or focus.make_source
        self._detect, self._make_source = detect, make_source
        self._store_factory = store_factory or (lambda: ActivityStore(timeout=WRITE_TIMEOUT))
        self._min_dwell, self._clock = min_dwell, clock
        self._is_paused = is_paused or recorder.is_paused
        self._queue: queue.Queue = queue.Queue(maxsize=1000)
        self._stopping = threading.Event()
        self._own_pids = {os.getpid(), os.getppid()}
        self._lock = threading.Lock()
        self._current: str | None = None  # last real app written (not excluded/none/paused)
        self._last_written: str | None = None
        self._last_raw = None             # last (app, private, pid) seen; never a title
        self._source = None
        self._writer = None
        self._retry = None
        self.backend: str | None = None
        self.reason: str | None = None
        self.dropped = 0

    # -- lifecycle -----------------------------------------------------------
    def start(self) -> bool:
        """
        Start listening. If no backend can be found right now (Hyprland not up yet, xprop
        not installed), keep retrying in the background (1 s, doubling to 30 s) until it
        works or stop() is called; status() explains why meanwhile. True if running now.
        """
        if self._try_start():
            return True
        self._retry = threading.Thread(target=self._retry_loop, name="WindowTrackerRetry", daemon=True)
        self._retry.start()
        return False

    def _try_start(self) -> bool:
        try:
            backend, reason = self._detect()
        except Exception as exc:
            backend, reason = None, f"Couldn't check for a window system ({type(exc).__name__})."
        self.backend, self.reason = backend, reason
        if not backend or self._stopping.is_set():
            return False
        try:  # the source first: it only queues, so nothing is lost before the writer runs
            source = self._make_source(backend, self._on_focus)
            source.start()
        except Exception as exc:
            self.reason = f"Couldn't start the {backend} listener ({type(exc).__name__})."
            return False
        self._source = source
        self._writer = threading.Thread(target=self._write_loop, name="WindowTrackerWriter", daemon=True)
        self._writer.start()
        recorder.set_active_app_provider(self.current_app)
        if self._stopping.is_set():  # stop() raced with this start: undo it
            self.stop()
            return False
        return True

    def _retry_loop(self) -> None:
        delay = 1.0
        while not self._stopping.wait(delay):
            if self._try_start():
                return
            delay = min(delay * 2, 30.0)

    def stop(self) -> None:
        # Mark the end, so the time after this isn't credited to the last app (Phase 2 dwell times).
        if self._last_written is not None and self._last_written not in privacy.MARKERS \
                and not self._stopping.is_set() and not self._is_paused():
            self._commit(datetime.now(timezone.utc), privacy.STOPPED)
        self._stopping.set()  # from here on the writer must not write anything
        recorder.clear_active_app_provider(self.current_app)
        src, self._source = self._source, None
        if src is not None:
            try:
                src.stop()
                src.join(timeout=3)
            except Exception:
                pass
        with self._queue.mutex:  # drop any backlog so the stop sentinel can't be lost
            self._queue.queue.clear()
        try:
            self._queue.put_nowait(_STOP)
        except queue.Full:
            pass
        writer, self._writer = self._writer, None
        if writer is not None and writer is not threading.current_thread():
            writer.join(timeout=3)
        retry, self._retry = self._retry, None
        if retry is not None and retry is not threading.current_thread():
            retry.join(timeout=3)

    def _source_failure(self) -> str | None:
        src = self._source
        return getattr(src, "failure", None) if src is not None else None

    @property
    def running(self) -> bool:
        return bool(not self._stopping.is_set() and self._writer and self._writer.is_alive()
                    and self._source and self._source.is_alive() and not self._source_failure())

    def status(self) -> dict:
        if self.running:
            return {"running": True, "backend": self.backend, "reason": None}
        reason = self._source_failure() or self.reason
        if reason is None and self.backend:
            reason = "The app listener stopped unexpectedly; turn tracking off and on again."
        return {"running": False, "backend": self.backend, "reason": reason}

    def current_app(self) -> str | None:
        """The app you're in (never Pikina), for the active_app of a command; None when paused or unhealthy."""
        if not self.running or self._is_paused():
            return None
        with self._lock:
            return self._current

    # -- source thread ---------------------------------------------------------
    def _on_focus(self, app, private, pid) -> None:
        """Called by the focus source. Must be cheap: hand over, with whether we were paused at that moment."""
        if self._stopping.is_set():
            return
        if not ((pid is not None and pid in self._own_pids) or privacy.is_pikina(app, canonical_app(app) if app else None)):
            self._last_raw = (app, bool(private), pid)  # the app to pick up again after a pause
        try:
            self._queue.put_nowait((datetime.now(timezone.utc), self._clock(), app, bool(private), pid,
                                    self._is_paused()))
        except queue.Full:
            self.dropped += 1

    # -- writer thread ---------------------------------------------------------
    def _label(self, app, private, pid) -> str | None:
        """None = ignore this event (Pikina's own window)."""
        browser = privacy.web_app_browser(app)
        canon = canonical_app(browser or app) if app else None
        if (pid is not None and pid in self._own_pids) or privacy.is_pikina(app, canon):
            return None
        if not app:
            return privacy.NOTHING
        if private or privacy.is_excluded(app, canon):
            return privacy.EXCLUDED
        return _token(canon) if canon else "?"

    def _commit(self, ts, label) -> bool:
        if self._stopping.is_set():
            return False
        try:
            store = self._store_factory()
            store.add_switch(ts=ts, app=label, backend=self.backend)
            recorder.maybe_prune(store)
        except Exception as exc:
            print(f"[Tracker] Not recorded: {type(exc).__name__}")
            return False
        with self._lock:
            self._current = None if label in privacy.MARKERS else label
            self._last_written = label
        return True

    def _write_loop(self) -> None:
        try:
            self._run_writer()
        except Exception as exc:  # never die silently: say why in status()
            self.reason = f"The tracker stopped ({type(exc).__name__}); turn tracking off and on again."
            with self._lock:
                self._current = None

    def _run_writer(self) -> None:
        pending = None      # (ts, mono, label) waiting to outlast MIN_DWELL
        last = None         # last label written
        was_paused = False
        while not self._stopping.is_set():
            try:
                item = self._queue.get(timeout=0.2 if pending else 1.0)
            except queue.Empty:
                item = None
            if item is _STOP or self._stopping.is_set():
                return

            paused_now = self._is_paused()
            if paused_now or (item is not None and item[5]):
                if not was_paused:
                    was_paused, pending = True, None
                    if last not in (None, privacy.PAUSED) and self._commit(datetime.now(timezone.utc), privacy.PAUSED):
                        last = privacy.PAUSED
                    with self._lock:
                        self._current = None
                continue
            if was_paused:  # resumed: you're already settled in the current app, so write it now
                was_paused = False
                if item is None and self._last_raw is not None:
                    label = self._label(*self._last_raw)
                    if label is not None and label != last and self._commit(datetime.now(timezone.utc), label):
                        last = label
                    continue

            if item is None:  # quiet: write the pending focus once it has lasted long enough
                if pending and self._clock() - pending[1] >= self._min_dwell:
                    if self._commit(pending[0], pending[2]):
                        last = pending[2]
                    pending = None
                continue
            ts, mono, app, private, pid, _ = item
            label = self._label(app, private, pid)
            if label is None:
                continue
            if pending:
                if label == pending[2]:
                    continue
                # Judge by when the next focus arrived, not by when we got round to it.
                if mono - pending[1] >= self._min_dwell and self._commit(pending[0], pending[2]):
                    last = pending[2]
                pending = None  # otherwise it was flicker: drop the short-lived focus
            if label == last:
                continue
            pending = (ts, mono, label)
