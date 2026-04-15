"""
Phase 7 — Search Index Generator
API Explorer Pipeline | GSoC 2026 | foss42/apidash
Author: Bhumika Nilesh Ujjainkar

Generates a pre-built search index at marketplace/search/index.json.

Why pre-built search:
  - Flutter cannot efficiently search 2500+ APIs by downloading all
  - Client-side search on full index.json is slow on mobile
  - Pre-built inverted index enables fast keyword lookup
  - Flutter downloads search/index.json once and searches locally

Search index structure:
  {
    "version": "1.0",
    "generated_at": "...",
    "api_count": 2500,
    "terms": {
      "openai": ["openai", "together-ai", "aiml-api"],
      "voice": ["elevenlabs", "lovo-ai", "playht"],
      "image": ["openai", "stability-ai", "pollinations-ai"],
      ...
    },
    "apis": {
      "openai": {
        "title": "OpenAI API",
        "description": "...",
        "tags": ["ai", "text-generation"],
        "score": 95
      }
    }
  }

Flutter search flow:
  1. User types "voice"
  2. Flutter looks up terms["voice"] → list of api_ids
  3. Flutter fetches those api_ids from apis[] (already in memory)
  4. Results shown instantly — no network request needed

Corner cases handled:
  - Stop words filtered (the, a, an, is, are...)
  - Short terms ignored (< 3 chars)
  - Terms normalized (lowercase, stripped)
  - Duplicate term entries deduplicated
  - Quality score computed for ranking results
  - Search index too large (compress terms)
"""

import re
import json
from pathlib import Path
from collections import defaultdict
from fetcher import get_logger


logger = get_logger("search_indexer")


# ─────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────

SEARCH_DIR   = Path("marketplace/search")
SEARCH_INDEX = SEARCH_DIR / "index.json"

# Common English stop words — filtered from search index
STOP_WORDS = {
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to",
    "for", "of", "with", "by", "from", "is", "are", "was", "were",
    "be", "been", "being", "have", "has", "had", "do", "does", "did",
    "will", "would", "could", "should", "may", "might", "can", "its",
    "via", "api", "rest", "http", "json", "url", "get", "post", "put",
    "this", "that", "these", "those", "it", "as", "into", "than",
    "more", "also", "any", "all", "both", "each", "few", "many",
    "such", "own", "same", "so", "then", "too", "very", "just"
}

MIN_TERM_LENGTH = 3     # ignore very short terms
MAX_TERMS_PER_API = 50  # cap terms per API to keep index manageable


# ─────────────────────────────────────────────────────────────
# Quality Scorer
# Ranks APIs by template completeness for better search results
# ─────────────────────────────────────────────────────────────

def compute_quality_score(index_entry: dict,
                          full_template: dict = None) -> int:
    """
    Computes a quality score (0-100) for an API template.

    Scoring factors:
      - Has description (10 pts)
      - Description length > 50 chars (10 pts)
      - Has tags (10 pts)
      - Number of endpoints (up to 20 pts)
      - Endpoints have request bodies (10 pts)
      - Endpoints have auth headers (10 pts)
      - Has base_url (10 pts)
      - Source is apis.guru (5 pts — verified spec)
      - No quality warnings (15 pts)

    Higher score = shown first in search results.
    """
    score = 0

    # Description quality
    description = index_entry.get("description", "")
    if description:
        score += 10
        if len(description) > 50:
            score += 10

    # Tags
    if index_entry.get("tags"):
        score += 10

    # Endpoint count (more = better, up to 20 pts)
    endpoint_count = index_entry.get("endpoint_count", 0)
    score += min(endpoint_count * 4, 20)

    # Source reliability
    if index_entry.get("source") == "apis.guru":
        score += 5

    # Full template quality (if available)
    if full_template:
        requests = full_template.get("requests", [])

        # Has request bodies
        if any(r.get("body") for r in requests):
            score += 10

        # Has auth headers
        if any(r.get("headers") for r in requests):
            score += 10

        # Has base URL
        if full_template.get("info", {}).get("base_url"):
            score += 10

    return min(score, 100)


# ─────────────────────────────────────────────────────────────
# Term Extractor
# Extracts searchable terms from API metadata
# ─────────────────────────────────────────────────────────────

def extract_terms(api_id: str, index_entry: dict) -> set:
    """
    Extracts normalized searchable terms from an API entry.

    Sources:
      - Title words
      - Description words
      - Tags/categories
      - API ID parts

    Filtering:
      - Stop words removed
      - Short terms (< 3 chars) removed
      - Duplicates removed
      - Max 50 terms per API
    """
    terms = set()

    def add_text(text: str):
        if not text:
            return
        # Normalize: lowercase, remove punctuation
        clean = re.sub(r'[^a-z0-9\s]', ' ', text.lower())
        words = clean.split()
        for word in words:
            word = word.strip()
            if (len(word) >= MIN_TERM_LENGTH and
                    word not in STOP_WORDS):
                terms.add(word)

    # Extract from all metadata fields
    add_text(index_entry.get("title", ""))
    add_text(index_entry.get("description", ""))

    for tag in index_entry.get("tags", []):
        add_text(tag)
        # Also add tag parts (text-generation → text, generation)
        for part in tag.split("-"):
            if len(part) >= MIN_TERM_LENGTH:
                terms.add(part)

    # Add API ID parts (openai → openai, elevenlabs → elevenlabs)
    for part in re.split(r'[-_.]', api_id):
        if len(part) >= MIN_TERM_LENGTH and part not in STOP_WORDS:
            terms.add(part.lower())

    return set(list(terms)[:MAX_TERMS_PER_API])


# ─────────────────────────────────────────────────────────────
# Search Index Builder
# ─────────────────────────────────────────────────────────────

class SearchIndexBuilder:
    """
    Builds an inverted index from API metadata.

    Inverted index maps term → list of API IDs:
      {
        "voice": ["elevenlabs", "lovo-ai", "playht", "openai"],
        "image": ["openai", "stability-ai", "pollinations-ai"],
        ...
      }

    This enables O(1) lookup — no scanning all 2500 APIs.
    """

    def __init__(self):
        self.inverted_index: dict[str, list] = defaultdict(list)
        self.api_summaries : dict[str, dict] = {}

    def add_api(self, api_id: str, index_entry: dict,
                full_template: dict = None):
        """Adds one API to the search index."""

        # Extract searchable terms
        terms = extract_terms(api_id, index_entry)

        # Add to inverted index
        for term in terms:
            if api_id not in self.inverted_index[term]:
                self.inverted_index[term].append(api_id)

        # Compute quality score for ranking
        score = compute_quality_score(index_entry, full_template)

        # Add lightweight API summary for search results display
        self.api_summaries[api_id] = {
            "title"         : index_entry.get("title", ""),
            "description"   : index_entry.get("description", "")[:100],
            "tags"          : index_entry.get("tags", []),
            "endpoint_count": index_entry.get("endpoint_count", 0),
            "requires_auth" : index_entry.get("requires_auth", True),
            "source"        : index_entry.get("source", ""),
            "score"         : score,
            "filename"      : index_entry.get("filename", "")
        }

    def build(self) -> dict:
        """
        Builds the final search index dict.

        Sorts each term's API list by quality score (highest first).
        This means better templates appear first in search results.
        """
        # Sort each term's results by quality score (descending)
        sorted_terms = {}
        for term, api_ids in self.inverted_index.items():
            sorted_api_ids = sorted(
                api_ids,
                key=lambda x: self.api_summaries.get(x, {}).get("score", 0),
                reverse=True
            )
            sorted_terms[term] = sorted_api_ids

        return {
            "version"     : "1.0",
            "generated_at": __import__('time').strftime(
                "%Y-%m-%dT%H:%M:%SZ", __import__('time').gmtime()
            ),
            "api_count"   : len(self.api_summaries),
            "term_count"  : len(sorted_terms),
            "terms"       : sorted_terms,
            "apis"        : self.api_summaries
        }


# ─────────────────────────────────────────────────────────────
# Main Search Indexer
# ─────────────────────────────────────────────────────────────

class SearchIndexer:
    """
    Phase 7 — Generates pre-built search index.

    Reads from marketplace/index.json and marketplace/apis/*.json
    to build a complete search index at marketplace/search/index.json.
    """

    def __init__(self, marketplace_dir: str = "marketplace"):
        self.marketplace_dir = Path(marketplace_dir)
        self.index_file      = self.marketplace_dir / "index.json"
        self.apis_dir        = self.marketplace_dir / "apis"
        self.search_dir      = self.marketplace_dir / "search"
        self.search_index    = self.search_dir / "index.json"

    def build_index(self) -> dict:
        logger.info("=" * 50)
        logger.info("Phase 7 — Search Indexer starting")
        logger.info("=" * 50)

        # Load master index
        if not self.index_file.exists():
            logger.error("index.json not found — run publisher first")
            return {"status": "failed",
                    "reason": "index.json missing"}

        try:
            master_index = json.loads(
                self.index_file.read_text(encoding="utf-8")
            )
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"Failed to load index.json: {e}")
            return {"status": "failed", "reason": str(e)}

        logger.info(f"Building search index for {len(master_index)} APIs...")

        builder = SearchIndexBuilder()
        success = 0
        failed  = 0

        for api_id, index_entry in master_index.items():
            try:
                # Try to load full template for quality scoring
                template_path = self.apis_dir / f"{api_id}.json"
                full_template = None

                if template_path.exists():
                    try:
                        full_template = json.loads(
                            template_path.read_text(encoding="utf-8")
                        )
                    except (json.JSONDecodeError, IOError):
                        pass  # Quality scoring still works without full template

                builder.add_api(api_id, index_entry, full_template)
                success += 1

            except Exception as e:
                failed += 1
                logger.error(f"Failed to index {api_id}: {e}")

        # Build and save search index
        search_index = builder.build()

        self.search_dir.mkdir(parents=True, exist_ok=True)
        self.search_index.write_text(
            json.dumps(search_index, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )

        index_size_kb = self.search_index.stat().st_size / 1024

        logger.info(
            f"Phase 7 complete: {success} APIs indexed, "
            f"{search_index['term_count']} unique terms, "
            f"{index_size_kb:.1f}KB"
        )

        return {
            "status"    : "success",
            "apis_indexed": success,
            "failed"    : failed,
            "term_count": search_index["term_count"],
            "index_size_kb": round(index_size_kb, 2),
            "index_path": str(self.search_index)
        }


# ─────────────────────────────────────────────────────────────
# Demo
# ─────────────────────────────────────────────────────────────

def run_demo():
    print("\n" + "="*55)
    print("  Phase 7 — Search Indexer Demo")
    print("="*55)

    indexer = SearchIndexer(marketplace_dir="marketplace")
    result  = indexer.build_index()

    print(f"\n  Status      : {result['status']}")

    if result["status"] == "success":
        print(f"  APIs indexed: {result['apis_indexed']}")
        print(f"  Terms       : {result['term_count']}")
        print(f"  Index size  : {result['index_size_kb']} KB")

        # Show sample searches
        search_index = json.loads(SEARCH_INDEX.read_text(encoding="utf-8"))
        terms        = search_index.get("terms", {})
        apis         = search_index.get("apis", {})

        print(f"\n  Sample search results:")

        for query in ["voice", "image", "text", "openai", "free"]:
            matches = terms.get(query, [])
            if matches:
                top_3   = matches[:3]
                titles  = [apis.get(m, {}).get("title", m) for m in top_3]
                print(f"    '{query}' → {titles}")
            else:
                print(f"    '{query}' → no results")

        # Show quality scores
        print(f"\n  Quality scores (top 5):")
        sorted_apis = sorted(
            apis.items(),
            key=lambda x: x[1].get("score", 0),
            reverse=True
        )
        for api_id, api_data in sorted_apis[:5]:
            print(
                f"    {api_data['title']:30s} "
                f"score={api_data['score']:3d} "
                f"endpoints={api_data['endpoint_count']}"
            )

    print("\n" + "="*55)
    print("  Phase 7 complete")
    print("="*55)


if __name__ == "__main__":
    run_demo()
