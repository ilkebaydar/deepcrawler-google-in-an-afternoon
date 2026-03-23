"""
Concurrent web crawler with lifecycle control (Pause/Stop/Resume).
Supports persistent state so stopped jobs can be resumed after restart.
"""

import html.parser
import json
import os
import queue
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from typing import Any, Optional

from file_manager import FileManager  # type: ignore[import-not-found]

WORD_PATTERN = re.compile(r"[a-zA-Z]{2,}")


class LinkParser(html.parser.HTMLParser):
    """Extracts and normalizes absolute href links from an HTML page."""

    def __init__(self, base_url: str):
        super().__init__()
        self.base_url = base_url
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                url = urllib.parse.urljoin(self.base_url, href)
                parsed = urllib.parse.urlparse(url)
                if parsed.scheme in ("http", "https"):
                    # Strip fragments and query strings for canonical URL form
                    clean_url = urllib.parse.urlunparse(
                        (parsed.scheme, parsed.netloc, parsed.path, "", "", "")
                    )
                    self.links.append(clean_url)


class TextParser(html.parser.HTMLParser):
    """Extracts visible text, suppressing script/style/head content."""

    _SUPPRESS = {"script", "style", "head", "noscript", "link", "meta"}

    def __init__(self):
        super().__init__()
        self.is_valid = True
        self.text_data: list[str] = []

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in self._SUPPRESS:
            self.is_valid = False

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SUPPRESS:
            self.is_valid = True

    def handle_data(self, data: str) -> None:
        if self.is_valid:
            self.text_data.append(data)

    @property
    def word_frequencies(self) -> Counter:
        raw = " ".join(self.text_data)
        words = re.findall(r"[a-zA-Z]{2,}", raw)
        return Counter(w.lower() for w in words)


class CrawlerJob:
    """A self-contained crawl job running on its own thread."""

    def __init__(
        self,
        origin_url: str,
        file_manager: FileManager,
        max_depth: int = 3,
        hit_rate: float = 1.0,
        max_urls_to_visit: Optional[int] = None,
        queue_capacity: int = 1000,
    ):
        self.crawler_id = ""
        self.origin_url = origin_url
        self.file_manager = file_manager
        self.max_depth = max_depth
        self.hit_rate = max(hit_rate, 0.1)
        self.max_urls_to_visit = max_urls_to_visit
        self.visited_urls: set[str] = set()

        self.queue: queue.Queue[tuple[str, int]] = queue.Queue(maxsize=queue_capacity)
        self.thread: Optional[threading.Thread] = None

        self._stop_event = threading.Event()
        self._pause_event = threading.Event()

        self.stats: dict[str, Any] = {
            "origin_url": origin_url,
            "max_depth": max_depth,
            "hit_rate": hit_rate,
            "max_urls_to_visit": max_urls_to_visit,
            "processed": 0,
            "words": 0,
            "status": "pending",
            "events": [],           # Last 50 terminal log entries
            "queue_snapshot": [],   # Pending URLs saved for resume
            "visited_snapshot": [], # Visited URLs saved for resume
        }

    def _log_event(self, message: str) -> None:
        """Append a timestamped entry to the event log (capped at 50)."""
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        self.stats["events"].append(f"{ts} - {message}")
        if len(self.stats["events"]) > 50:
            self.stats["events"].pop(0)

    def start(self) -> None:
        """Initialize the queue and start the crawl thread."""
        if not self.crawler_id:
            self.queue.put((self.origin_url, 0))
            self.crawler_id = f"{int(time.time())}_{threading.get_ident()}"

        self._stop_event.clear()
        self._pause_event.clear()
        t = threading.Thread(target=self._run, daemon=True)
        self.thread = t
        t.start()

    def _run(self) -> None:
        """Main crawl loop: fetch, parse, index, and enqueue child links."""
        self.stats["status"] = "running"
        self._log_event(f"Processing queue: {self.queue.qsize()} items left.")

        while not self.queue.empty() and not self._stop_event.is_set():
            # Pause loop: spin until unpaused or stopped
            while self._pause_event.is_set() and not self._stop_event.is_set():
                self.stats["status"] = "paused"
                self.file_manager.log_job_status(self.crawler_id, self.stats)
                time.sleep(0.5)

            if self._stop_event.is_set():
                break
            self.stats["status"] = "running"

            if self.max_urls_to_visit and self.stats["processed"] >= self.max_urls_to_visit:
                self._log_event(f"Reached visit limit ({self.max_urls_to_visit}). Finishing...")
                break

            try:
                url, depth = self.queue.get(timeout=1)
            except queue.Empty:
                break

            if url not in self.visited_urls:
                self.visited_urls.add(url)
                self._log_event(f"Crawling {url} at depth {depth}")
                try:
                    req = urllib.request.Request(url, headers={"User-Agent": "DeepCrawler/1.0"})
                    with urllib.request.urlopen(req, timeout=5) as res:
                        self._log_event(f"Successfully accessed {url}")

                        if "text/html" in res.headers.get("Content-Type", ""):
                            content = res.read().decode("utf-8", errors="replace")

                            lp, tp = LinkParser(url), TextParser()
                            lp.feed(content)
                            tp.feed(content)

                            freqs = tp.word_frequencies
                            for w, f in freqs.items():
                                self.file_manager.index_word(w, url, self.origin_url, depth, f)

                            self.stats["processed"] += 1
                            self.stats["words"] += len(freqs)
                            self._log_event(f"Stored {len(freqs)} unique words from {url}")

                            if depth < self.max_depth:
                                new_urls = 0
                                for link in lp.links:
                                    try:
                                        self.queue.put((link, depth + 1), block=False)
                                        new_urls += 1
                                    except queue.Full:
                                        break
                                self._log_event(f"Found {new_urls} new URLs at {url}")
                except Exception as e:
                    self._log_event(f"Error accessing {url}: {str(e)}")

            # Snapshot queue and visited set every cycle for resume support
            snapshot = [list(item) for item in list(self.queue.queue)]
            self.stats["queue_snapshot"] = snapshot
            self.stats["queue_size"] = len(snapshot)
            self.stats["visited_snapshot"] = list(self.visited_urls)
            self.file_manager.log_job_status(self.crawler_id, self.stats)
            time.sleep(1.0 / self.hit_rate)

        # Distinguish user-initiated stop from natural completion
        if self._stop_event.is_set():
            self.stats["status"] = "stopped"
        else:
            self.stats["status"] = "completed"
            # Clear snapshots — nothing left to resume from
            self.stats["queue_snapshot"] = []
            self.stats["visited_snapshot"] = []

        self._log_event(f"Job {self.stats['status']}. Processed {self.stats['processed']} URLs.")
        self.file_manager.log_job_status(self.crawler_id, self.stats)


class CrawlerManager:
    """Orchestrates multiple CrawlerJob instances."""

    def __init__(self, file_manager: FileManager):
        self.file_manager = file_manager
        self._jobs: dict[str, CrawlerJob] = {}
        self._lock = threading.Lock()

    def spawn(self, origin_url: str, **kwargs: Any) -> CrawlerJob:
        """Create and immediately start a new crawler job."""
        job = CrawlerJob(origin_url, self.file_manager, **kwargs)
        job.start()
        with self._lock:
            self._jobs[job.crawler_id] = job
        return job

    def stop_job(self, crawler_id: str) -> bool:
        """Signal a running job to stop. Progress is saved to disk."""
        with self._lock:
            job = self._jobs.get(crawler_id)
        if job:
            job.stats["status"] = "stopped"
            job._stop_event.set()
            job._pause_event.clear()
            return True
        return False

    def pause_job(self, crawler_id: str) -> bool:
        """Pause a running job without losing its queue position."""
        with self._lock:
            job = self._jobs.get(crawler_id)
        if job and job.stats["status"] == "running":
            job.stats["status"] = "paused"
            job._pause_event.set()
            return True
        return False

    def resume_job(self, crawler_id: str) -> bool:
        """
        Resume a job. Two cases are handled:
        1. In-memory paused job: simply clear the pause event.
        2. Stopped job (e.g., after server restart): reconstruct from disk snapshot.
        """
        with self._lock:
            job = self._jobs.get(crawler_id)

        # Case 1: job is paused in memory
        if job and job._pause_event.is_set():
            job._pause_event.clear()
            return True

        # Case 2: reconstruct a stopped job from its persisted log file
        log_path = os.path.join(self.file_manager.LOGS_DIR, f"{crawler_id}.data")
        if not os.path.exists(log_path):
            return False

        try:
            with open(log_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            if data.get("status") != "stopped":
                return False

            queue_snapshot = data.get("queue_snapshot", [])
            visited_snapshot = data.get("visited_snapshot", [])

            if not queue_snapshot and not visited_snapshot:
                # Queue was empty when stopped — nothing to resume
                return False

            new_job = CrawlerJob(
                origin_url=data.get("origin_url", ""),
                file_manager=self.file_manager,
                max_depth=int(data.get("max_depth", 3)),
                hit_rate=float(data.get("hit_rate", 1.0)),
                max_urls_to_visit=data.get("max_urls_to_visit"),
                queue_capacity=max(len(queue_snapshot) + 100, 1000),
            )
            # Reuse the original ID so the log file is updated in-place
            new_job.crawler_id = crawler_id

            # Restore counters and event history from saved state
            new_job.stats["processed"] = data.get("processed", 0)
            new_job.stats["words"] = data.get("words", 0)
            new_job.stats["events"] = data.get("events", [])

            new_job.visited_urls = set(visited_snapshot)

            for item in queue_snapshot:
                try:
                    new_job.queue.put_nowait((item[0], item[1]))
                except queue.Full:
                    break

            new_job._log_event(
                f"Resumed from snapshot: {new_job.queue.qsize()} URLs in queue, "
                f"{len(new_job.visited_urls)} already visited."
            )
            new_job.start()

            with self._lock:
                self._jobs[crawler_id] = new_job
            return True

        except Exception:
            return False

    def get_status_all(self) -> dict[str, Any]:
        """Return a snapshot of all in-memory job states including live queue sizes."""
        with self._lock:
            status_map: dict[str, Any] = {}
            for cid, job in self._jobs.items():
                data = dict(job.stats)
                data["crawler_id"] = cid
                data["queue_size"] = job.queue.qsize()
                status_map[cid] = data
            return status_map

    def delete_job(self, crawler_id: str) -> bool:
        """Stop and remove a job from memory (log file deletion is handled separately)."""
        self.stop_job(crawler_id)
        with self._lock:
            if crawler_id in self._jobs:
                self._jobs = {k: v for k, v in self._jobs.items() if k != crawler_id}
                return True
        return False