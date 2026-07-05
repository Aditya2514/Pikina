import os
import threading
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from core.mcm.provenance import UNTRUSTED

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
        # We don't want to publish temp files from browsers like .crdownload or .tmp
        ext = os.path.splitext(path)[1].lower()
        if ext in ('.crdownload', '.tmp', '.part'):
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
        
        home = Path.home()
        self.paths_to_watch = [
            home / "Desktop",
            home / "Downloads",
            home / "OneDrive" / "Desktop",
            home / "OneDrive" / "Downloads",
        ]

    def start(self):
        for p in self.paths_to_watch:
            if p.exists():
                self.observer.schedule(self.handler, str(p), recursive=True)
        
        self.observer.start()

    def stop(self):
        self.observer.stop()
        self.observer.join(timeout=2.0)
