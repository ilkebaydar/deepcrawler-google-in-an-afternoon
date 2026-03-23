"""
HTTP server acting as the control center for Brightwave.
Handles: Dashboard stats, Crawler lifecycle, Search, Autocomplete, and Log management.
"""

import http.server
import json
import os
import sys
import socketserver
import urllib.parse
import time
from typing import Any

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from crawler import CrawlerManager    # type: ignore[import-not-found]
from file_manager import FileManager  # type: ignore[import-not-found]
from searcher import Searcher

HOST: str = "localhost"
PORT: int = 3600
TEMPLATES_DIR: str = os.path.join(os.path.dirname(__file__), "templates")

# Module-level singletons shared across all request threads
file_manager: FileManager = FileManager()
crawler_manager: CrawlerManager = CrawlerManager(file_manager)
searcher: Searcher = Searcher()


class ReusableTCPServer(socketserver.ThreadingTCPServer):
    """Allows immediate port reuse to prevent 'Address already in use' errors."""
    allow_reuse_address = True


class RequestHandler(http.server.BaseHTTPRequestHandler):
    """Routes all HTTP requests to the appropriate handler method."""

    def log_message(self, format: str, *args: Any) -> None:
        pass  # Suppress access logs for cleaner output

    # -------------------------------------------------------------------------
    # Routing
    # -------------------------------------------------------------------------

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/":
            self._serve_template("index.html")
        elif path == "/api/dashboard":
            self._handle_api_dashboard()
        elif path == "/api/search":
            self._handle_api_search(parsed.query)
        elif path == "/api/autocomplete":
            self._handle_api_autocomplete(parsed.query)
        elif path.startswith("/api/logs/"):
            self._handle_api_logs(path)
        elif path.startswith("/api/download/"):
            self._handle_api_download(path)
        else:
            self.send_error(404)

    def do_POST(self) -> None:
        path = self.path
        if path == "/api/spawn":
            self._handle_api_spawn()
        elif path.startswith("/api/stop/"):
            self._handle_lifecycle_command(path, "stop")
        elif path.startswith("/api/pause/"):
            self._handle_lifecycle_command(path, "pause")
        elif path.startswith("/api/resume/"):
            self._handle_lifecycle_command(path, "resume")
        else:
            self.send_error(404)

    def do_DELETE(self) -> None:
        if self.path.startswith("/api/logs/"):
            self._handle_api_delete_log(self.path)

    # -------------------------------------------------------------------------
    # Handler Implementations
    # -------------------------------------------------------------------------

    def _handle_api_dashboard(self) -> None:
        """Aggregate live job states and persisted log files into one dashboard payload."""
        total_words = 0
        s_dir = file_manager.STORAGE_DIR
        if os.path.exists(s_dir):
            for fname in os.listdir(s_dir):
                if fname.endswith(".data"):
                    try:
                        with open(os.path.join(s_dir, fname), "r", encoding="utf-8") as fh:
                            total_words += sum(1 for line in fh if line.strip())
                    except:
                        pass

        # Read all persisted job logs from disk
        status_map: dict[str, Any] = {}
        l_dir = file_manager.LOGS_DIR
        if os.path.exists(l_dir):
            for fname in os.listdir(l_dir):
                if fname.endswith(".data") and not fname.endswith(".tmp"):
                    cid = fname.removesuffix(".data")
                    try:
                        with open(os.path.join(l_dir, fname), "r", encoding="utf-8") as fh:
                            data = json.load(fh)
                            status_map[cid] = data
                            status_map[cid]["crawler_id"] = cid
                    except:
                        pass

        live_jobs = crawler_manager.get_status_all()

        # If the server was restarted, mark orphaned "running" logs as stopped
        live_keys = [str(k) for k in live_jobs.keys()]
        for cid, data in status_map.items():
            if data.get("status") in ["running", "paused"] and str(cid) not in live_keys:
                data["status"] = "stopped"

        # Merge live data on top of persisted data (live always wins)
        for cid, data in live_jobs.items():
            scid = str(cid)
            if scid in status_map:
                status_map[scid].update(data)
            else:
                status_map[scid] = data

        active_jobs = sum(1 for j in status_map.values() if j.get("status") in ["running", "paused"])

        self._send_json({
            "global_metrics": {
                "total_words": total_words,
                "total_jobs": len(status_map),
                "active_jobs": active_jobs,
            },
            "jobs": status_map,
        })

    def _handle_api_download(self, path: str) -> None:
        """Serve the crawler event log as a downloadable .txt file."""
        cid = path.split("/")[-1]
        log_path = os.path.join(file_manager.LOGS_DIR, f"{cid}.data")
        if os.path.exists(log_path):
            with open(log_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            events = data.get("events", [])
            body = "\n".join(events).encode("utf-8")

            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="brightwave_log_{cid}.txt"')
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404)

    def _handle_lifecycle_command(self, path: str, command: str) -> None:
        """Route stop / pause / resume signals to the CrawlerManager."""
        cid = path.split("/")[-1]
        success = False
        if command == "stop":
            success = crawler_manager.stop_job(cid)
        elif command == "pause":
            success = crawler_manager.pause_job(cid)
        elif command == "resume":
            success = crawler_manager.resume_job(cid)

        if success:
            self._send_json({"status": "ok", "crawler_id": cid})
        else:
            self.send_error(404, f"Could not {command} crawler {cid}")

    def _handle_api_spawn(self) -> None:
        """Parse request body and create a new crawler job."""
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))

        job = crawler_manager.spawn(
            origin_url=body.get("url", ""),
            max_depth=int(body.get("depth", 3)),
            hit_rate=float(body.get("hit_rate", 1.0)),
            max_urls_to_visit=int(body["max_urls"]) if body.get("max_urls") else None,
            queue_capacity=int(body.get("queue_capacity", 1000)),
        )
        self._send_json({"crawler_id": job.crawler_id, "status": "spawned"})

    def _handle_api_search(self, query_str: str) -> None:
        """Run a ranked word search and return paginated results."""
        params = urllib.parse.parse_qs(query_str)
        q = params.get("q", [""])[0]
        limit = int(params.get("limit", ["10"])[0])
        offset = int(params.get("offset", ["0"])[0])

        total, raw_results = searcher.search(q, limit=limit, offset=offset)
        results = [{"url": r[0], "origin": r[1], "depth": r[2], "frequency": r[3]} for r in raw_results]
        self._send_json({"results": results, "total": total})

    def _handle_api_autocomplete(self, query_str: str) -> None:
        """Return word suggestions from the index for the given prefix."""
        params = urllib.parse.parse_qs(query_str)
        q = params.get("q", [""])[0]
        suggestions = searcher.autocomplete(q)
        self._send_json({"suggestions": suggestions})

    def _handle_api_logs(self, path: str) -> None:
        """Return the full log file content for a given crawler ID."""
        cid = path.split("/")[-1]
        log_path = os.path.join(file_manager.LOGS_DIR, f"{cid}.data")
        if os.path.exists(log_path):
            with open(log_path, "r", encoding="utf-8") as f:
                self._send_json(json.load(f))
        else:
            self.send_error(404)

    def _handle_api_delete_log(self, path: str) -> None:
        """Remove a job from memory and delete its log file from disk."""
        cid = path.split("/")[-1]
        crawler_manager.delete_job(cid)

        log_path = os.path.join(file_manager.LOGS_DIR, f"{cid}.data")
        if os.path.exists(log_path):
            os.remove(log_path)

        self._send_json({"status": "deleted"})

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    def _serve_template(self, filename: str) -> None:
        path = os.path.join(TEMPLATES_DIR, filename)
        if os.path.exists(path):
            with open(path, "rb") as f:
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(f.read())
        else:
            self.send_error(404)

    def _send_json(self, data: Any) -> None:
        body = json.dumps(data).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)


def run_server() -> None:
    os.makedirs(TEMPLATES_DIR, exist_ok=True)
    with ReusableTCPServer((HOST, PORT), RequestHandler) as server:
        print(f"[*] DeepCrawler Control Center live at http://{HOST}:{PORT}")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            server.shutdown()


if __name__ == "__main__":
    run_server()