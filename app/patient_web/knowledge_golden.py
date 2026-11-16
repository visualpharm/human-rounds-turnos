"""Golden conversations for the wiki (the product owner, 2026-09-05).

The booking chat answers from the wiki through `knowledge.rag_context()`.
A wiki edit — a retitled article, a dropped tag, a category unpublished by
mistake — can silently stop a question from finding its article, and no
existing test notices: the chat still answers, just from nothing.

A golden conversation is a real patient question plus what the retrieval
MUST return for it: the article ids that have to be among the sources and
the facts (plain substrings, accent- and case-insensitive) the RAG context
has to contain. The set is DATA, composed across the active pack chain
(`conversaciones-doradas.json`, registered in `data_packs._COMPOSABLE`),
because each pack knows which of its own articles are load-bearing.

Deterministic on purpose: no AI call, so it runs on every CI run and from
the wiki screen ("Probar conversaciones", `POST
/api/admin/content/knowledge/golden`) in well under a second.
"""

from app.core import data_packs
from app.patient_web import knowledge

FILE = "conversaciones-doradas.json"


def conversations():
    """The composed golden set, in pack order."""
    if not data_packs.is_composable(FILE):
        return []
    return [c for c in data_packs.compose(FILE) if c.get("question")]


def _plain(value):
    return knowledge._plain(value or "")


def check(conversation, today=None):
    """One verdict: {id, question, ok, missing_articles, missing_facts, found}."""
    rag = knowledge.rag_context(conversation["question"])
    articles = knowledge.published_articles(today)
    by_title = {a.get("title"): a.get("id") for a in articles}
    found = [by_title.get(f.get("titulo")) or f.get("titulo") for f in rag["fuentes"]]
    missing_articles = [a for a in (conversation.get("articles") or []) if a not in found]
    text = _plain(rag["texto"])
    missing_facts = [f for f in (conversation.get("facts") or []) if _plain(f) not in text]
    return {
        "id": conversation.get("id"),
        "question": conversation["question"],
        "ok": not missing_articles and not missing_facts,
        "found": found,
        "missing_articles": missing_articles,
        "missing_facts": missing_facts,
    }


def run(today=None):
    """Every golden conversation checked against the live wiki."""
    results = [check(c, today) for c in conversations()]
    return {"total": len(results), "passed": sum(1 for r in results if r["ok"]),
            "results": results}
