"""Comparable past transcripts for an event, and the event's own transcript (for checking the matcher)."""
import datetime as dt
import re

from . import events, sources

LIMITS = {"trump-rally": 10, "trump-other": 12, "earnings": 8}
OTHER_TYPES = {"Remarks", "Speech", "Press Conference"}


def _is_rally(doc):
    return "Rally" in doc["title"] and "Tele" not in doc["title"]


class Corpus:
    def __init__(self, since):
        """`since`: YYYY-MM-DD, earliest event date to cover. Factbase goes back 120 days
        before it (enough for the latest comparables), Fool two years (8 quarterly calls)."""
        day = dt.date.fromisoformat(since)
        self.factbase = sources.factbase_index((day - dt.timedelta(days=120)).isoformat())
        self.fool = sources.fool_index((day - dt.timedelta(days=730)).isoformat()[:7])
        self._company = {}

    def company_slug(self, name):
        if name not in self._company:
            self._company[name] = sources.match_company(name, self.fool)
        return self._company[name]

    def _docs(self, event):
        if event["kind"] == "earnings":
            slugs = self.company_slug(event["company"])
            return [r for r in reversed(self.fool) if r["company"] in slugs]
        want = _is_rally if event["kind"] == "trump-rally" else (lambda d: d["type"] in OTHER_TYPES and not _is_rally(d))
        return [d for d in self.factbase if want(d)]

    def _text(self, event, doc):
        return sources.fool_text(doc) if event["kind"] == "earnings" else sources.trump_text(doc)

    def comparables(self, event):
        """Texts of the most recent comparable transcripts strictly before the event day, newest first."""
        day = event["start"].astimezone(events.ET).date().isoformat()
        docs = [d for d in self._docs(event) if d["date"] < day]
        texts = (self._text(event, d) for d in docs)
        return [t for t in texts if t][: LIMITS[event["kind"]]]

    def actual(self, event):
        """The event's own transcript if it can be identified unambiguously, else None."""
        day = event["start"].astimezone(events.ET).date()
        if event["kind"] == "earnings":
            days = {day.isoformat(), (day + dt.timedelta(days=1)).isoformat()}
            hits = [d for d in self._docs(event) if d["date"] in days]
        elif event["kind"] == "trump-rally":
            place = re.search(r" in ([^?]+)\?", event["title"])
            hits = [d for d in self._docs(event) if d["date"] == day.isoformat()
                    and (not place or place.group(1).split()[-1] in d["title"])]
        else:
            return None
        if len(hits) != 1:
            return None
        return self._text(event, hits[0]) or None
