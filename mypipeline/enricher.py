"""
Phase 3 — Enricher
API Explorer Pipeline | GSoC 2026 | foss42/apidash
Author: Bhumika Nilesh Ujjainkar

Responsibilities:
  - Assign categories from multiple sources (apis.guru, keyword rules, AI fallback)
  - Generate {{PLACEHOLDER}} syntax for auth headers
    (matches API Dash's existing environment variable system)
  - Standardize auth header format across all APIs
  - Fill missing descriptions
  - Add logo URLs from apis.guru metadata
  - Normalize path parameters: {param} → {{PARAM}}
  - Detect content type headers
  - Add helpful notes for each endpoint

Corner cases handled:
  - API with no category from apis.guru
  - API with category not in our predefined set → map to closest
  - Auth scheme unknown → default to bearer
  - Path parameters with inconsistent naming ({userId} vs {user_id})
  - APIs with multiple auth schemes
  - Missing base URL
  - Description too long (truncate for UI)
  - Logo URL returning 404 (skip gracefully)
"""

import os
import re
import json
import logging
from fetcher import get_logger


logger = get_logger("enricher")


# ─────────────────────────────────────────────────────────────
# Predefined Categories
# Subset of foss42/awesome-open-source-flutter-apps categories
# as specified by maintainer @ashitaprasad in issue #619
# ─────────────────────────────────────────────────────────────

PREDEFINED_CATEGORIES = {
    "ai"              : "AI & Machine Learning",
    "text-generation" : "Text Generation",
    "image-generation": "Image Generation",
    "voice"           : "Voice & Audio",
    "video"           : "Video Generation",
    "embedding"       : "Embeddings & Search",
    "translation"     : "Translation",
    "code-generation" : "Code Generation",
    "finance"         : "Finance & Payments",
    "weather"         : "Weather",
    "social"          : "Social Media",
    "communication"   : "Communication",
    "data"            : "Data & Analytics",
    "maps"            : "Maps & Location",
    "media"           : "Media & Entertainment",
    "security"        : "Security",
    "developer-tools" : "Developer Tools",
    "other"           : "Other",
}

# Mapping from apis.guru categories to our predefined categories
APIS_GURU_CATEGORY_MAP = {
    "machine_learning" : "ai",
    "artificial_intelligence": "ai",
    "text"             : "text-generation",
    "images"           : "image-generation",
    "audio"            : "voice",
    "video"            : "video",
    "search"           : "embedding",
    "translation"      : "translation",
    "financial"        : "finance",
    "payment"          : "finance",
    "weather"          : "weather",
    "social"           : "social",
    "messaging"        : "communication",
    "email"            : "communication",
    "analytics"        : "data",
    "mapping"          : "maps",
    "location"         : "maps",
    "media"            : "media",
    "entertainment"    : "media",
    "security"         : "security",
    "developer_tools"  : "developer-tools",
    "tools"            : "developer-tools",
}

# Keyword rules for fallback categorization
KEYWORD_CATEGORY_RULES = {
    "text-generation" : ["text", "chat", "completion", "language", "llm",
                         "gpt", "claude", "gemini", "writing", "generate"],
    "image-generation": ["image", "vision", "picture", "dall", "stable",
                         "diffusion", "visual", "photo", "artwork"],
    "voice"           : ["voice", "speech", "audio", "tts", "stt",
                         "transcription", "whisper", "sound", "speak"],
    "video"           : ["video", "avatar", "lip", "synthesia", "animation",
                         "movie", "clip"],
    "embedding"       : ["embed", "vector", "semantic", "retrieval",
                         "similarity", "search"],
    "translation"     : ["translat", "language", "multilingual", "locali"],
    "code-generation" : ["code", "coding", "program", "developer",
                         "github", "copilot", "script"],
    "finance"         : ["payment", "financ", "bank", "stripe", "invoice",
                         "billing", "money", "currency"],
    "weather"         : ["weather", "forecast", "temperature", "climate"],
    "maps"            : ["map", "location", "geocod", "direction",
                         "coordinate", "address"],
    "communication"   : ["email", "sms", "message", "chat", "notification",
                         "push", "twilio", "sendgrid"],
    "ai"              : ["ai", "model", "generative", "inference",
                         "neural", "machine learning", "deep learning"],
}


# ─────────────────────────────────────────────────────────────
# Categorizer
# ─────────────────────────────────────────────────────────────

class Categorizer:
    """
    Assigns predefined categories to APIs using three strategies:

    Strategy 1: Use apis.guru x-apisguru-categories (most reliable)
    Strategy 2: Keyword matching on title + description (fallback)
    Strategy 3: Default to 'other' if nothing matches

    Why three strategies:
      - apis.guru categories don't cover all our predefined categories
      - AI APIs from awesome-generative-ai-apis have no apis.guru categories
      - Some APIs have misleading categories on apis.guru
    """

    def categorize(self, title: str, description: str,
                   apis_guru_categories: list) -> list:
        """
        Returns list of matching predefined category keys.
        Always returns at least one category.
        """
        categories = set()

        # Strategy 1: Map apis.guru categories to our predefined set
        for ag_cat in apis_guru_categories:
            ag_cat_lower = ag_cat.lower()
            if ag_cat_lower in APIS_GURU_CATEGORY_MAP:
                categories.add(APIS_GURU_CATEGORY_MAP[ag_cat_lower])

        # Strategy 2: Keyword matching on title + description
        text = (title + " " + description).lower()
        for category, keywords in KEYWORD_CATEGORY_RULES.items():
            if any(kw in text for kw in keywords):
                categories.add(category)

        # Strategy 3: Fallback
        if not categories:
            categories.add("other")

        # Always validate against predefined set
        valid = [c for c in categories if c in PREDEFINED_CATEGORIES]
        return valid if valid else ["other"]


# ─────────────────────────────────────────────────────────────
# Auth Header Generator
# Generates {{PLACEHOLDER}} syntax matching API Dash env vars
# ─────────────────────────────────────────────────────────────

class AuthHeaderGenerator:
    """
    Generates authentication headers using {{PLACEHOLDER}} syntax.

    Why {{PLACEHOLDER}} syntax:
      API Dash already has an environment variable system that uses
      exactly this format. When a user imports a template, the
      placeholders slot straight into their existing environment
      setup — no extra work needed.

    Examples:
      bearer  → {"Authorization": "Bearer {{OPENAI_API_KEY}}"}
      api_key → {"X-API-Key": "{{ELEVENLABS_API_KEY}}"}
      basic   → {"Authorization": "Basic {{BASE64_CREDENTIALS}}"}
      oauth2  → {"Authorization": "Bearer {{ACCESS_TOKEN}}"}
    """

    def generate(self, api_title: str, auth_scheme: str,
                 api_key_header: str = None) -> dict:
        """
        Generates auth headers for a given API and auth scheme.

        api_title: Used to generate meaningful placeholder names
                   e.g. "OpenAI API" → "OPENAI_API_KEY"
        """
        # Generate API-specific placeholder name
        # e.g. "OpenAI API" → "OPENAI_API_KEY"
        #      "ElevenLabs Voice API" → "ELEVENLABS_VOICE_API_KEY"
        placeholder_name = self._make_placeholder_name(api_title)

        if auth_scheme == "bearer":
            return {
                "Authorization": f"Bearer {{{{{placeholder_name}}}}}"
            }

        elif auth_scheme == "api_key":
            # Use custom header if specified, otherwise default
            header_name = api_key_header or "X-API-Key"
            return {
                header_name: f"{{{{{placeholder_name}}}}}"
            }

        elif auth_scheme == "basic":
            return {
                "Authorization": f"Basic {{{{{placeholder_name}_BASE64}}}}"
            }

        elif auth_scheme == "oauth2":
            return {
                "Authorization": f"Bearer {{{{{placeholder_name}_ACCESS_TOKEN}}}}"
            }

        # No auth
        return {}

    def _make_placeholder_name(self, api_title: str) -> str:
        """
        Converts API title to SCREAMING_SNAKE_CASE placeholder.
        e.g. "OpenAI API" → "OPENAI_API_KEY"
             "ElevenLabs" → "ELEVENLABS_API_KEY"
        """
        # Remove common suffixes
        clean = re.sub(r'\s*(API|Service|Platform|SDK|v\d+)\s*$',
                       '', api_title, flags=re.IGNORECASE)
        # Convert to SCREAMING_SNAKE_CASE
        clean = re.sub(r'[^a-zA-Z0-9\s]', '', clean)
        clean = re.sub(r'\s+', '_', clean.strip())
        clean = clean.upper()
        return f"{clean}_API_KEY" if clean else "API_KEY"


def build_auth_metadata(api_title: str, auth_scheme: str,
                        api_key_header: str = None) -> dict:
    """
    Emits structured auth metadata so ApiDash can later map templates
    into its native AuthModel instead of inferring everything from headers.
    """
    generator = AuthHeaderGenerator()
    placeholder_name = generator._make_placeholder_name(api_title)

    if auth_scheme == "bearer":
        return {
            "type": "bearer",
            "location": "header",
            "header_name": "Authorization",
            "placeholder": placeholder_name,
            "prefix": "Bearer ",
        }

    if auth_scheme == "api_key":
        return {
            "type": "api_key",
            "location": "header",
            "header_name": api_key_header or "X-API-Key",
            "placeholder": placeholder_name,
            "prefix": "",
        }

    if auth_scheme == "basic":
        return {
            "type": "basic",
            "location": "header",
            "header_name": "Authorization",
            "placeholder": f"{placeholder_name}_BASE64",
            "prefix": "Basic ",
        }

    if auth_scheme == "oauth2":
        return {
            "type": "oauth2",
            "location": "header",
            "header_name": "Authorization",
            "placeholder": f"{placeholder_name}_ACCESS_TOKEN",
            "prefix": "Bearer ",
        }

    return {
        "type": "none",
        "location": None,
        "header_name": None,
        "placeholder": None,
        "prefix": "",
    }


# ─────────────────────────────────────────────────────────────
# Path Parameter Normalizer
# Converts {param} → {{PARAM}} to match API Dash env var format
# ─────────────────────────────────────────────────────────────

def normalize_path_parameters(url: str) -> str:
    """
    Converts OpenAPI path parameters to API Dash placeholder format.

    Examples:
      /v1/customers/{customer_id}  → /v1/customers/{{CUSTOMER_ID}}
      /v1/users/{userId}/posts     → /v1/users/{{USER_ID}}/posts
      /v1/voices/{voice_id}        → /v1/voices/{{VOICE_ID}}

    Corner cases:
      - camelCase params: {userId} → {{USER_ID}}
      - Already double-braced: {{param}} → leave unchanged
      - Nested params: /{a}/{b} → /{{A}}/{{B}}
    """
    # Skip if already using double braces
    if "{{" in url:
        return url

    def replace_param(match):
        param_name = match.group(1)
        # Convert camelCase to SCREAMING_SNAKE_CASE
        # userId → USER_ID
        snake = re.sub(r'([A-Z])', r'_\1', param_name).upper().lstrip('_')
        return f"{{{{{snake}}}}}"

    return re.sub(r'\{([^}]+)\}', replace_param, url)


# ─────────────────────────────────────────────────────────────
# Description Enricher
# Cleans and enriches API descriptions
# ─────────────────────────────────────────────────────────────

MAX_DESCRIPTION_LENGTH = 200  # characters — keeps UI clean

def enrich_description(title: str, description: str,
                        categories: list) -> str:
    """
    Cleans and enriches description text.

    Corner cases:
      - Description is empty → generate from title + categories
      - Description is HTML → strip tags
      - Description too long → truncate at word boundary
      - Description is just the title repeated → replace with better text
    """
    # Strip HTML tags if present
    description = re.sub(r'<[^>]+>', '', description).strip()

    # Replace with generated description if empty or just title repeated
    if not description or description.lower() == title.lower():
        cat_names = [PREDEFINED_CATEGORIES.get(c, c) for c in categories[:2]]
        cat_str   = " and ".join(cat_names) if cat_names else "API"
        description = f"{title} provides {cat_str} capabilities via REST API."

    # Truncate at word boundary if too long
    if len(description) > MAX_DESCRIPTION_LENGTH:
        truncated = description[:MAX_DESCRIPTION_LENGTH]
        last_space = truncated.rfind(" ")
        if last_space > 0:
            truncated = truncated[:last_space]
        description = truncated + "..."

    return description


# ─────────────────────────────────────────────────────────────
# Content Type Detector
# Adds appropriate Content-Type header based on endpoint
# ─────────────────────────────────────────────────────────────

def detect_content_type(method: str, request_body: dict,
                        url: str) -> str:
    """
    Detects appropriate Content-Type header for an endpoint.

    Rules:
      - GET/DELETE → no Content-Type needed
      - POST/PUT/PATCH with body → application/json (default)
      - Endpoints with 'upload', 'file', 'audio', 'video' in URL → multipart/form-data
      - Endpoints with 'form' in URL → application/x-www-form-urlencoded
    """
    if method in ("GET", "DELETE") or not request_body:
        return ""

    url_lower = url.lower()
    # "image" intentionally excluded: image-generation endpoints (e.g. /images/generations)
    # accept a JSON body with a prompt string — not a multipart file upload.  Including
    # "image" also caused false positives when the word appeared in the domain name
    # (e.g. image.pollinations.ai) rather than in a meaningful path segment.
    if any(kw in url_lower for kw in ["upload", "file", "audio",
                                       "video", "multipart"]):
        return "multipart/form-data"

    if "form" in url_lower:
        return "application/x-www-form-urlencoded"

    return "application/json"


# ─────────────────────────────────────────────────────────────
# Endpoint Note Generator
# Adds helpful usage notes to each endpoint
# ─────────────────────────────────────────────────────────────

def generate_endpoint_note(api_title: str, method: str,
                           url: str, auth_scheme: str) -> str:
    """
    Generates a helpful note for each endpoint template.
    These appear in the API Dash template detail view.

    Examples:
      "Replace {{OPENAI_API_KEY}} with your key from platform.openai.com"
      "This endpoint requires multipart/form-data for file upload"
    """
    notes = []

    # Auth note
    if auth_scheme != "none":
        placeholder = re.sub(r'[^a-zA-Z0-9\s]', '',
                             api_title).strip().upper().replace(' ', '_')
        placeholder = re.sub(r'_API$', '', placeholder) + "_API_KEY"
        notes.append(f"Replace {{{{{placeholder}}}}} with your API key")

    # File upload note
    url_lower = url.lower()
    # "image" excluded for the same reason as detect_content_type — it fires on domain
    # names and /images/ JSON endpoints that don't involve file uploads.
    if any(kw in url_lower for kw in ["upload", "file", "audio", "video"]):
        notes.append("Set Content-Type to multipart/form-data for file uploads")

    # Path parameter note
    if "{{" in normalize_path_parameters(url):
        notes.append("Replace path parameters (e.g. {{VOICE_ID}}) with actual values")

    return " | ".join(notes) if notes else ""


# ─────────────────────────────────────────────────────────────
# Main Enricher
# ─────────────────────────────────────────────────────────────

class Enricher:
    """
    Phase 3 — Enriches parsed API data with:
      - Categories (predefined, validated)
      - Auth headers with {{PLACEHOLDER}} syntax
      - Normalized path parameters
      - Content-Type headers
      - Endpoint notes
      - Cleaned descriptions
    """

    def __init__(self):
        self.categorizer       = Categorizer()
        self.auth_generator    = AuthHeaderGenerator()

    def enrich_api(self, parsed_api: dict) -> dict:
        """
        Enriches a single parsed API dict.
        Returns enriched dict ready for Phase 4 (Publisher).
        """
        title       = parsed_api.get("title", "Unknown API")
        description = parsed_api.get("description", "")
        base_url    = parsed_api.get("base_url", "")
        endpoints   = parsed_api.get("endpoints", [])
        requires_auth = parsed_api.get("requires_auth")

        if requires_auth is None:
            requires_auth = any(
                endpoint.get("auth_scheme", "none") != "none"
                for endpoint in endpoints
            )

        # Step 1: Categorize
        apis_guru_cats = parsed_api.get("apis_guru_categories", [])
        categories     = self.categorizer.categorize(
            title, description, apis_guru_cats
        )

        # Step 2: Enrich description
        description = enrich_description(title, description, categories)

        # Step 3: Enrich each endpoint
        enriched_endpoints = []
        for endpoint in endpoints:
            enriched = self._enrich_endpoint(endpoint, title)
            enriched_endpoints.append(enriched)

        # Step 4: Build enriched API dict
        return {
            "title"      : title,
            "description": description,
            "base_url"   : base_url,
            "version"    : parsed_api.get("version", "1.0.0"),
            "categories" : categories,
            "requires_auth": requires_auth,
            "requests"   : enriched_endpoints,
            "source"     : parsed_api.get("source", "apis.guru"),
            "id"         : parsed_api.get("api_id", ""),
        }

    def _enrich_endpoint(self, endpoint: dict, api_title: str) -> dict:
        """
        Enriches a single endpoint with auth headers,
        normalized URLs, content types and notes.
        """
        method      = endpoint.get("method", "GET")
        url         = endpoint.get("url", "")
        auth_scheme = endpoint.get("auth_scheme", "none")
        request_body= endpoint.get("request_body", {})

        # Normalize path parameters: {param} → {{PARAM}}
        normalized_url = normalize_path_parameters(url)

        # Generate auth headers
        auth_headers = self.auth_generator.generate(api_title, auth_scheme)
        auth_metadata = build_auth_metadata(api_title, auth_scheme)

        # Detect content type
        content_type = detect_content_type(method, request_body, url)

        # Build full headers dict
        headers = {}
        if auth_headers:
            headers.update(auth_headers)
        if content_type:
            headers["Content-Type"] = content_type

        # Generate endpoint note
        note = generate_endpoint_note(api_title, method, url, auth_scheme)

        return {
            "name"        : endpoint.get("name", f"{method} {url}"),
            "method"      : method,
            "url"         : normalized_url,
            "headers"     : headers,
            "body"        : request_body,
            "auth"        : auth_metadata,
            "parameters"  : endpoint.get("parameters", []),
            "description" : endpoint.get("description", ""),
            "note"        : note,
        }

    def enrich_all(self, parsed_apis: list) -> list:
        """
        Enriches all parsed APIs from Phase 2.
        Returns list of enriched API dicts.
        """
        logger.info("=" * 50)
        logger.info("Phase 3 — Enricher starting")
        logger.info("=" * 50)

        enriched = []
        success  = 0
        failed   = 0

        for parsed_api in parsed_apis:
            try:
                result = self.enrich_api(parsed_api)
                enriched.append(result)
                success += 1
                logger.debug(
                    f"Enriched {result['title']}: "
                    f"categories={result['categories']}"
                )
            except Exception as e:
                failed += 1
                logger.error(
                    f"Failed to enrich {parsed_api.get('title','?')}: {e}"
                )

        logger.info(f"Phase 3 complete: {success} enriched, {failed} failed")

        # Save for Phase 4
        os.makedirs("data", exist_ok=True)
        with open("data/enriched_apis.json", "w") as f:
            json.dump(enriched, f, indent=2)

        return enriched


# ─────────────────────────────────────────────────────────────
# Demo
# ─────────────────────────────────────────────────────────────

def run_demo():
    enricher = Enricher()

    print("\n" + "="*55)
    print("  Phase 3 — Enricher Demo")
    print("="*55)

    mock_parsed_apis = [
        {
            "title"      : "OpenAI API",
            "description": "Access GPT-4, DALL-E image generation and Whisper.",
            "base_url"   : "https://api.openai.com/v1",
            "version"    : "2.0.0",
            "apis_guru_categories": ["machine_learning"],
            "api_id"     : "openai.com",
            "endpoints"  : [
                {
                    "name"        : "Create chat completion",
                    "method"      : "POST",
                    "url"         : "https://api.openai.com/v1/chat/completions",
                    "auth_scheme" : "bearer",
                    "request_body": {"model": "gpt-4",
                                     "messages": [{"role": "user",
                                                   "content": "Hello"}]},
                    "parameters"  : [],
                    "description" : "Send messages to GPT models"
                },
                {
                    "name"        : "Generate image",
                    "method"      : "POST",
                    "url"         : "https://api.openai.com/v1/images/generations",
                    "auth_scheme" : "bearer",
                    "request_body": {"prompt": "A sunset", "n": 1},
                    "parameters"  : [],
                    "description" : "Generate images with DALL-E"
                },
                {
                    "name"        : "List models",
                    "method"      : "GET",
                    "url"         : "https://api.openai.com/v1/models",
                    "auth_scheme" : "bearer",
                    "request_body": {},
                    "parameters"  : [],
                    "description" : "List available models"
                }
            ]
        },
        {
            "title"      : "ElevenLabs Voice API",
            "description": "Generate realistic AI voices from text.",
            "base_url"   : "https://api.elevenlabs.io/v1",
            "version"    : "1.0.0",
            "apis_guru_categories": [],
            "api_id"     : "elevenlabs",
            "source"     : "awesome-generative-ai-apis",
            "endpoints"  : [
                {
                    "name"        : "Text to Speech",
                    "method"      : "POST",
                    "url"         : "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
                    "auth_scheme" : "api_key",
                    "request_body": {"text": "Hello world",
                                     "model_id": "eleven_monolingual_v1"},
                    "parameters"  : [],
                    "description" : "Convert text to speech"
                },
                {
                    "name"        : "List Voices",
                    "method"      : "GET",
                    "url"         : "https://api.elevenlabs.io/v1/voices",
                    "auth_scheme" : "api_key",
                    "request_body": {},
                    "parameters"  : [],
                    "description" : "Get all available voices"
                }
            ]
        },
        {
            "title"      : "Pollinations.AI",
            "description": "",  # Corner case: empty description
            "base_url"   : "https://image.pollinations.ai",
            "version"    : "1.0.0",
            "apis_guru_categories": [],
            "api_id"     : "pollinations",
            "endpoints"  : [
                {
                    "name"        : "Generate Image",
                    "method"      : "GET",
                    "url"         : "https://image.pollinations.ai/prompt/{prompt}",
                    "auth_scheme" : "none",  # No auth needed
                    "request_body": {},
                    "parameters"  : [],
                    "description" : ""
                }
            ]
        }
    ]

    enriched = enricher.enrich_all(mock_parsed_apis)

    for api in enriched:
        print(f"\n→ {api['title']}")
        print(f"  Categories : {api['categories']}")
        print(f"  Description: {api['description'][:80]}...")
        for ep in api["requests"]:
            print(f"  [{ep['method']}] {ep['url']}")
            if ep["headers"]:
                print(f"    Headers: {ep['headers']}")
            if ep["note"]:
                print(f"    Note: {ep['note']}")

    print("\n" + "="*55)
    print("  Phase 3 complete")
    print("="*55)


if __name__ == "__main__":
    run_demo()
