# DeepCrawler — Web Crawler & Search Engine

A lightweight web crawler and full-text search engine built **entirely with Python's standard library** — no external frameworks, no third-party parsers.

This project was developed for the **ITU AI Aided Computer Engineering** course, specifically for the *"Google in an Afternoon"* assignment, instructed by **Asst. Prof. Dr. Yusuf Hüseyin Şahin**. The system's architecture and design choices were additionally informed by industry insights provided by guest speaker **David Doğukan Erenel**.

---

## Built With

To accelerate development and ensure a robust architecture, the following tools and AI models were utilized during the creation of this project:
* **Antigravity** — Primary IDE / Development Environment
* **Gemini 3.1 Pro** — Architecture planning, system design, and optimization
* **Claude Sonnet 4.6** — Advanced logic implementation and code refinement

---

## What It Does

You give it a URL. It crawls the web starting from that URL, indexes every word it finds, and lets you search through all of it in real time.

All of this happens through a local web dashboard you open in your browser.

---

## Features

| Feature | Details |
|---|---|
| **Web Crawling** | Follows `<a href>` links recursively up to a configurable max depth |
| **Concurrent Jobs** | Multiple crawlers run simultaneously on separate threads |
| **Lifecycle Control** | Pause, Resume, and Stop any running job from the UI |
| **Persistent Resume** | Stopped jobs save their queue to disk — resume exactly where you left off, even after restarting the server |
| **Full-Text Search** | Ranked by word frequency (descending), tie-broken by page depth (ascending) |
| **Autocomplete** | Live word suggestions as you type, pulled directly from the indexed data |
| **Search Pagination** | Navigate through all results page by page |
| **Real-Time Dashboard** | Live stats: indexed words, active jobs, queue depth, and event logs |
| **No Duplicate Crawls** | The UI warns you if you try to crawl a URL that's already been indexed |

---

## Project Structure

    crawler/
    ├── main.py           # HTTP server & API routing — the control center
    ├── crawler.py        # Crawl engine: threading, lifecycle control, queue persistence
    ├── searcher.py       # Read-only search engine: ranking, pagination, autocomplete
    ├── file_manager.py   # Thread-safe disk I/O: shard writes, atomic job log updates
    ├── templates/
    │   └── index.html    # Dashboard UI — vanilla HTML/CSS/JS, no frameworks
    ├── storage/          # Sharded word index (a.data, b.data, …)
    │                     #   Each line: { word, current_url, origin_url, depth, frequency }
    ├── logs/             # One JSON log file per crawler job
    │                     #   Stores: status, counters, events, queue snapshot, visited snapshot
    ├── product_prd.md    # Full product requirements document
    ├── recommendation.md # Future scaling and Phase 2 architecture recommendations
    └── .gitignore

---

## How to Run

**Requirements:** Python 3.7 or higher. No `pip install` needed.

    python main.py

Then open your browser at: **http://localhost:3600**

---

## How to Use

### Start a Crawler
1. In the left sidebar under **Start Crawler**, enter a URL (e.g. `https://wikipedia.org`).
2. Configure depth, hit rate and URL limits.
3. Click **Start Crawler Job**.

The crawler appears in the **Running Now** section and updates in real time.

### Search
1. Click on **Search Index** in the sidebar.
2. Type a keyword — autocomplete suggestions appear as you type.
3. Press Enter or click **Search Engine**.

Results show the matching URL, origin site, depth, and word frequency.  
Use **Previous** / **Next** to page through results. Click **✕ Close** to return to the dashboard.

### Resume a Stopped Job
1. Stop a running crawler (click Stop and confirm).
2. Restart the server if needed — the job will appear in **Historical Records** with a `stopped` badge.
3. Click **↺ Resume** — the crawler picks up exactly where it left off, skipping all already-visited URLs.

> **Note:** Jobs that `completed` naturally cannot be resumed (their queue is already empty).

---

## Storage Design

### Word Index (`storage/`)
Each word is stored in a shard file named after its first letter:

    storage/a.data  →  words starting with 'a'
    storage/p.data  →  words starting with 'p'
    ...

Each line is a JSON record:

    { "word": "python", "current_url": "...", "origin_url": "...", "depth": 1, "frequency": 7 }


### Job Logs (`logs/`)
Each crawler job produces one file:

    logs/1711224000_140234567890.data

This file stores status, progress counters, the last 50 event log entries, and the full queue/visited snapshot for resume support.
