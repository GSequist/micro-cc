"""
File system watcher - detects changes in project directory.
Uses watchdog to monitor file changes and buffers them for injection into context.
"""

import os
import threading
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# Patterns to ignore
IGNORE_PATTERNS = {
    '.git', '__pycache__', '.pyc', '.pyo', '.swp', '.swo',
    '.DS_Store', 'node_modules', '.env', '.venv', 'env', 'venv',
    '.idea', '.vscode', '*.log', '.micro-cc'
}

def should_ignore(path: str) -> bool:
    """Check if path should be ignored."""
    parts = Path(path).parts
    for part in parts:
        if part in IGNORE_PATTERNS:
            return True
        for pattern in IGNORE_PATTERNS:
            if pattern.startswith('*') and part.endswith(pattern[1:]):
                return True
    return False


class ChangeHandler(FileSystemEventHandler):
    """Buffers file changes for later retrieval."""

    def __init__(self, project_dir: str):
        self.project_dir = project_dir
        self.changes = []  # list of (event_type, relative_path)
        self._lock = threading.Lock()

    def _record(self, event_type: str, path: str):
        if should_ignore(path):
            return
        # Make path relative to project
        try:
            rel_path = os.path.relpath(path, self.project_dir)
        except ValueError:
            rel_path = path

        with self._lock:
            # Dedupe: don't add same file+event twice
            entry = (event_type, rel_path)
            if entry not in self.changes:
                self.changes.append(entry)

    def on_modified(self, event):
        if not event.is_directory:
            self._record('modified', event.src_path)

    def on_created(self, event):
        if not event.is_directory:
            self._record('created', event.src_path)

    def on_deleted(self, event):
        if not event.is_directory:
            self._record('deleted', event.src_path)

    def on_moved(self, event):
        if not event.is_directory:
            self._record('moved', f"{event.src_path} → {event.dest_path}")

    def drain(self) -> list:
        """Get and clear all buffered changes."""
        with self._lock:
            changes = self.changes.copy()
            self.changes.clear()
            return changes


class FileWatcher:
    """Manages filesystem watching for a project directory."""

    def __init__(self, project_dir: str):
        self.project_dir = os.path.abspath(project_dir)
        self.handler = ChangeHandler(self.project_dir)
        self.observer = Observer()
        self._started = False

    def start(self):
        """Start watching the project directory."""
        if self._started:
            return
        self.observer.schedule(self.handler, self.project_dir, recursive=True)
        self.observer.start()
        self._started = True

    def stop(self):
        """Stop watching."""
        if self._started:
            self.observer.stop()
            self.observer.join(timeout=1)
            self._started = False

    def get_changes(self) -> list:
        """Get buffered changes without clearing."""
        with self.handler._lock:
            return self.handler.changes.copy()

    def drain_changes(self) -> list:
        """Get and clear buffered changes."""
        return self.handler.drain()

    def format_changes(self) -> str | None:
        """Drain changes and format as context string. Returns None if no changes."""
        changes = self.drain_changes()
        if not changes:
            return None

        lines = ["<file-changes>"]
        for event_type, path in changes:
            lines.append(f"  {event_type}: {path}")
        lines.append("</file-changes>")
        return "\n".join(lines)
