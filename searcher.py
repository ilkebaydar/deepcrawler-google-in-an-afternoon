"""
Read-only search engine over the sharded word index in `storage/`.

The index is written by CrawlerJob via FileManager as one JSON object per line.
Searcher opens relevant shard files in read-only mode and never acquires write
locks, so it is safe to run concurrently with an active crawl.
Partial lines from concurrent writes are silently skipped via per-line try/except.
"""

import itertools
import json
import os
import re
import threading
from typing import Any

# Anchor storage path to this file's location so searches work regardless
# of which directory the server process was launched from.
STORAGE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "storage")
WORD_PATTERN = re.compile(r"[a-zA-Z]{2,}")


class Searcher:
    """
    Searches the sharded flat-file word index.

    Usage:
        searcher = Searcher()
        total, results = searcher.search("python web crawler", limit=10, offset=0)
        # results -> list of (current_url, origin_url, depth, frequency)
    """

    def __init__(self) -> None:
        self._search_lock = threading.Lock()

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    def search(
        self,
        query: str,
        limit: int = 10,
        offset: int = 0,
    ) -> tuple[int, list[tuple[str, str, int, int]]]:
        """
        Search for all words in *query* and return ranked, paginated results.

        Algorithm:
          1. Tokenize query into words.
          2. For each word, scan its shard file for matching entries.
          3. Merge duplicate (word, url) pairs keeping highest frequency and shallowest depth.
          4. Sort by frequency descending, then depth ascending.
          5. Apply offset/limit pagination.

        Returns:
            (total_count, [(current_url, origin_url, depth, frequency), ...])
        """
        query_words = self._tokenise(query)
        if not query_words:
            return 0, []

        # key: (word, url) → best matching entry
        aggregated: dict[tuple[str, str], dict[str, Any]] = {}

        for word in query_words:
            for entry in self._lookup_word(word):
                key = (entry["word"], entry["current_url"])
                existing = aggregated.get(key)
                if existing is None:
                    aggregated[key] = entry
                else:
                    if entry["frequency"] > existing["frequency"]:
                        existing["frequency"] = entry["frequency"]
                    if entry["depth"] < existing["depth"]:
                        existing["depth"] = entry["depth"]

        ranked = sorted(aggregated.values(), key=lambda e: (-e["frequency"], e["depth"]))
        page = list(itertools.islice(ranked, offset, offset + limit))

        return len(ranked), [(e["current_url"], e["origin_url"], e["depth"], e["frequency"]) for e in page]

    def autocomplete(self, prefix: str, limit: int = 5) -> list[str]:
        """Return up to *limit* indexed words that start with *prefix*."""
        prefix = prefix.lower().strip()
        if not prefix:
            return []

        shard_file = self._shard_path(prefix)
        if not os.path.exists(shard_file):
            return []

        suggestions: set[str] = set()
        try:
            with open(shard_file, "r", encoding="utf-8") as fh:
                for raw_line in fh:
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        word = entry.get("word", "")
                        if isinstance(word, str) and word.startswith(prefix):
                            suggestions.add(word)
                            if len(suggestions) >= limit * 3:
                                break  # Collected enough candidates
                    except (json.JSONDecodeError, AttributeError):
                        pass
        except OSError:
            pass

        ranked = sorted(list(suggestions), key=len)
        return list(itertools.islice(ranked, limit))

    # -------------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------------

    def _tokenise(self, text: str) -> list[str]:
        """Extract unique lowercase words from *text* using the crawler's regex."""
        words = WORD_PATTERN.findall(text.lower())
        seen: set[str] = set()
        unique: list[str] = []
        for word in words:
            if word not in seen:
                seen.add(word)
                unique.append(word)
        return unique

    def _shard_path(self, word: str) -> str:
        """Return the storage shard path for the given word's first letter."""
        first = word[0] if word else "_"
        if not first.isalpha():
            first = "_"
        return os.path.join(STORAGE_DIR, f"{first}.data")

    def _lookup_word(self, word: str) -> list[dict[str, Any]]:
        """
        Scan the shard file for all entries matching *word* exactly.
        Malformed lines (from concurrent writes) are silently skipped.
        """
        shard_file = self._shard_path(word)
        if not os.path.exists(shard_file):
            return []

        matches: list[dict[str, Any]] = []
        try:
            with open(shard_file, "r", encoding="utf-8") as fh:
                for raw_line in fh:
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        if entry.get("word") == word:
                            matches.append(entry)
                    except (json.JSONDecodeError, AttributeError):
                        continue  # Skip partial writes from concurrent crawlers
        except OSError:
            pass

        return matches
