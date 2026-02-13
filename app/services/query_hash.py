from __future__ import annotations

import hashlib
import json

from app.schemas import SearchCreateRequest

_CACHE_KEY_VERSION = "v3"


def build_query_hash(query: SearchCreateRequest) -> str:
    payload = query.model_dump(mode="json")
    payload["sources"] = sorted([source.lower() for source in payload.get("sources") or []])
    canonical = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def cache_key_for_query_hash(query_hash: str) -> str:
    return f"search-result:{_CACHE_KEY_VERSION}:{query_hash}"
