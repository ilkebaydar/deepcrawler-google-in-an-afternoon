"""
file_manager.py
---------------
Thread-safe file I/O layer for sharded word index and job log storage.
"""

import json
import os
import threading


class FileManager:
    """Manages sharded word index files and crawler job log files."""

    # Anchor data directories to this file's location so they are always found
    # regardless of which directory the server process was launched from.
    _BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    STORAGE_DIR = os.path.join(_BASE_DIR, "storage")
    LOGS_DIR = os.path.join(_BASE_DIR, "logs")

    def __init__(self) -> None:
        os.makedirs(self.STORAGE_DIR, exist_ok=True)
        os.makedirs(self.LOGS_DIR, exist_ok=True)

        # Per-shard and per-job locks to allow concurrent crawlers
        self._shard_locks: dict[str, threading.Lock] = {}
        self._shard_locks_meta = threading.Lock()
        self._log_locks: dict[str, threading.Lock] = {}
        self._log_locks_meta = threading.Lock()

    def _get_shard_lock(self, letter: str) -> threading.Lock:
        if letter in self._shard_locks:
            return self._shard_locks[letter]
        with self._shard_locks_meta:
            if letter not in self._shard_locks:
                self._shard_locks[letter] = threading.Lock()
            return self._shard_locks[letter]

    def _get_log_lock(self, crawler_id: str) -> threading.Lock:
        if crawler_id in self._log_locks:
            return self._log_locks[crawler_id]
        with self._log_locks_meta:
            if crawler_id not in self._log_locks:
                self._log_locks[crawler_id] = threading.Lock()
            return self._log_locks[crawler_id]

    def index_word(self, word: str, current_url: str, origin_url: str, depth: int, frequency: int) -> None:
        """Append a word entry to the appropriate shard file (e.g., storage/a.data)."""
        if not word:
            return
        first_letter = word[0].lower()
        if not first_letter.isalpha():
            first_letter = "_"

        entry = {
            "word": word,
            "current_url": current_url,
            "origin_url": origin_url,
            "depth": depth,
            "frequency": frequency,
        }
        shard_path = os.path.join(self.STORAGE_DIR, f"{first_letter}.data")
        with self._get_shard_lock(first_letter):
            with open(shard_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def log_job_status(self, crawler_id: str, status_data: dict) -> None:
        """Atomically overwrite the job's log file using a temp-file swap."""
        log_path = os.path.join(self.LOGS_DIR, f"{crawler_id}.data")
        tmp_path = log_path + ".tmp"
        with self._get_log_lock(crawler_id):
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(status_data, f, indent=2, ensure_ascii=False)
            os.replace(tmp_path, log_path)