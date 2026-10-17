import os
import threading
from pathlib import Path

from pikina import paths
from pikina.osbridge import user_dirs


def _ignored_roots():
    roots = [paths.data_dir().resolve(), paths.PACKAGE_DIR]
    checkout = paths.source_checkout_root()
    if checkout:
        roots.append(checkout.resolve())
    return roots
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from pikina.mcm.provenance import UNTRUSTED

class _FileEventHandler(FileSystemEventHandler):
    def __init__(self, bus):
        super().__init__()
        self.bus = bus

    def on_created(self, event):
        if not event.is_directory:
            self._publish_event("fs.file_created", event.src_path)

    def on_modified(self, event):
        if not event.is_directory:
            self._publish_event("fs.file_modified", event.src_path)

    def _publish_event(self, topic, path):
        # 1. Ignore browser temp files
        ext = os.path.splitext(path)[1].lower()
        if ext in {'.crdownload', '.tmp', '.part'}:
            return

        # 2. Ignore Pikina's own files (data folder, code, source checkout) to prevent feedback loops
        resolved = str(Path(path).resolve())
        if any(resolved.startswith(str(root)) for root in _ignored_roots()):
            return

        self.bus.publish(
            topic=topic,
            payload={"path": path},
            provenance=UNTRUSTED
        )

class FileWatcherDaemon:
    def __init__(self, bus):
        self.bus = bus
        self.observer = Observer()
        self.handler = _FileEventHandler(self.bus)
        
        self.paths_to_watch = user_dirs(include_documents=False)

    def start(self):
        for p in self.paths_to_watch:
            if p.exists():
                self.observer.schedule(self.handler, str(p), recursive=True)
        
        self.observer.start()

    def stop(self):
        self.observer.stop()
        self.observer.join(timeout=2.0)

