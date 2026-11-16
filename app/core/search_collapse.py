"""search_collapse.py — the ONE rule that glues a visitor's typing fragments into the search they meant.

Typed letter by letter, a search lands as «ped», «pedi», «pediat». Within one typing session, a text that the
NEXT one extends, logged within `GLUE_SECONDS`, is a fragment and does not count; the longest does. Two readers
apply it: `core/db_analytics.top_searches` (the `searches` table, in SQL) and `analytics_report` (the stream,
in Python as the rows pass); both take the window and the prefix test from here. The direction differs on
purpose and as before: the table reader drops a row only when the NEXT one extends it, the stream reader
also keeps the longer text when the visitor backspaces (`related`).
"""

GLUE_SECONDS = 30  # pause between two settled fragments of one typing session


def extends(shorter, longer):
    """Does `longer` continue `shorter` (the same text counts)? The prefix test of the rule; the SQL side
    expresses it as `starts_with(longer, shorter)`."""
    return longer.startswith(shorter)


def related(a, b):
    """Either one extends the other: a visitor who typed on and then backspaced is still one search."""
    return extends(a, b) or extends(b, a)
