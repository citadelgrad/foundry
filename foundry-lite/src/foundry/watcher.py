from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import threading
from pathlib import Path
import sys
import time

from .runner import run_profile


class FoundryWatcher:
    def __init__(self, repo: Path, profile: str, debounce: float):
        self.repo = repo
        self.profile = profile
        self.debounce = debounce
        self._observer = Observer()
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()

    def start(self):
        run_profile(self.profile, repo_path=self.repo)
        print("[foundry] watching for changes (Ctrl-C to stop)...")

        watcher = self

        class _Handler(FileSystemEventHandler):
            def on_any_event(self, event):
                watcher._schedule_rerun(event)

        self._observer.schedule(_Handler(), str(self.repo), recursive=True)
        self._observer.start()

        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            self.stop()
            sys.exit(0)

    def stop(self):
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
        self._observer.stop()
        self._observer.join()

    def _schedule_rerun(self, event):
        src = getattr(event, "src_path", "") or ""
        if ".foundry" in src:
            return
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self.debounce, self._do_rerun)
            self._timer.start()

    def _do_rerun(self):
        print("-" * 60)
        run_profile(self.profile, repo_path=self.repo)
        print("[foundry] watching for changes (Ctrl-C to stop)...")
