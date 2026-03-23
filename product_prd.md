# Product Requirements Document (PRD)
**Project Name:** Google in One Day (ITU — AI Aided Computer Engineering)
**Overview:** A lightweight, fully concurrent Web Crawler and Search Engine (project codename: **DeepCrawler**) built strictly with Python's standard library. The goal is to mimic the core functionalities of web indexing and ranked search without any external scraping or parsing frameworks.

---

## 1. Core Identity & Strict Constraints
- **Language:** Python (3.7+)
- **Allowed Libraries:** `urllib`, `html.parser`, `re`, `threading`, `queue`, `json`, `os`, `time`, `http.server`, etc.
- **Restricted Libraries:** Scrapy, BeautifulSoup, Selenium, and any third-party fetching/parsing tool.

---

## 2. Storage & Persistence

### 2.1. Sharded Word Index (`storage/`)
- Words are stored by first letter: e.g., `"apple"` → `storage/a.data`.
- Each line is a JSON entry: `{ word, current_url, origin_url, depth, frequency }`.

### 2.2. Job Logs (`logs/`)
- **Crawler ID Format:** `[EpochTime]_[ThreadID]`
- Each job produces a `logs/<crawlerId>.data` file (JSON) containing:  
  `status`, `processed`, `words`, `events`, `queue_snapshot`, `visited_snapshot`, and configuration parameters.
- **Atomic writes:** A `.tmp` file is written first, then replaced atomically to prevent corruption.

### 2.3. Uniqueness — Visited Set
- Each `CrawlerJob` maintains an in-memory visited URL set.
- The UI prevents spawning a new job with a URL that already exists in any historical record.

---

## 3. Functional Requirements

### 3.1. Indexer (Crawler)
- **Recursive Traversal:** Follows `<a href>` links up to a configurable max depth.
- **Back Pressure:** Fixed-capacity `queue.Queue` to prevent memory overflow.
- **Rate Limiting:** Configurable hits per second (`hit_rate`).
- **Lifecycle Control:** Each job supports Pause, Resume, and Stop commands issued via the UI.
- **Persistent Resume:** When a job is stopped (manually or via server shutdown), its queue state and visited URL set are saved to disk. The job can be **resumed from exactly where it stopped** — even after a full server restart.
- **Status Distinction:** `"completed"` = naturally finished; `"stopped"` = user-interrupted (resumable).

### 3.2. Searcher
- **Live Concurrent Search:** Searches the `storage/` directory safely while crawls are in progress.
- **Ranking:** Results ranked by `frequency` (descending), ties broken by `depth` (ascending).
- **Pagination:** `offset` + `limit` parameters for page-based navigation.
- **Autocomplete:** Live prefix-matching against the word index, debounced in the UI (200ms).
- **Result Metadata:** Each result exposes `url`, `origin`, `depth`, and `frequency`.

### 3.3. Web UI Dashboard
Built using `http.server` and standard JavaScript polling (no frontend frameworks).

- **Global Stats Bar:** Indexed words, active jobs, and total job history.
- **Running Now Grid:** Live cards for each active or paused crawler with real-time stats.
- **Historical Records Grid:** Completed and stopped jobs with Delete and Resume actions.
- **Terminal Panel:** Right-side log viewer showing the last 50 timestamped crawler events.
- **Search Overlay:** Opens alongside the dashboard in a split-pane. Shows "About N Results (query)" header and paginated results with URL, origin, depth, and frequency.
- **Sidebar Accordion Panels:** "Start Crawler" and "Search Index" sections are visually distinct, always visible, and scrollable independently.

---

## 4. Technical Architecture

| Layer | Module | Responsibility |
|---|---|---|
| API Server | `main.py` | HTTP routing, request parsing, JSON responses |
| Crawler Engine | `crawler.py` | Thread-per-job crawling with lifecycle control & persistence |
| Search Engine | `searcher.py` | Read-only ranked word lookup with prefix autocomplete |
| Storage Manager | `file_manager.py` | Thread-safe shard file writes and atomic job log updates |
| Frontend | `templates/index.html` | Dashboard UI, polling, search, terminal panel |

---

## 5. API Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Serve the dashboard HTML |
| `GET` | `/api/dashboard` | Return global metrics and all job states |
| `GET` | `/api/search?q=&offset=&limit=` | Ranked paginated search results |
| `GET` | `/api/autocomplete?q=` | Word prefix suggestions |
| `GET` | `/api/logs/<id>` | Full log file for a job |
| `GET` | `/api/download/<id>` | Download event log as `.txt` |
| `POST` | `/api/spawn` | Create and start a new crawler job |
| `POST` | `/api/stop/<id>` | Stop a job and save its state |
| `POST` | `/api/pause/<id>` | Pause a running job |
| `POST` | `/api/resume/<id>` | Resume a paused job OR reconstruct a stopped job from disk |
| `DELETE` | `/api/logs/<id>` | Delete a job record from disk and memory |

---

