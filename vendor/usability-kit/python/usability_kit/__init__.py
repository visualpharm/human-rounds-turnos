"""usability-kit Python package: simulated participants, study compilation,
dashboard, and host helpers that mirror the Node findings store.

Modules are importable as ``usability_kit.<name>``; the LLM client is GLM Flash
over OpenRouter (``usability_kit.openrouter``).
"""
from .host import (  # noqa: F401
    claim_run,
    enqueue_run,
    evidence_path,
    finish_run,
    ingest,
    ingest_preflight,
    ingest_study,
    key_of,
    load_store,
    mark_fixed,
    norm_page,
    save_store,
    set_rating,
    signature,
    store_json,
    worst,
)

__all__ = [
    "claim_run", "enqueue_run", "evidence_path", "finish_run", "ingest",
    "ingest_preflight", "ingest_study", "key_of", "load_store", "mark_fixed",
    "norm_page", "save_store", "set_rating", "signature", "store_json", "worst",
]
