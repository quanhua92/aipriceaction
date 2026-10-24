"""Optional daily retention maintenance using the existing archive writer."""

import logging
from datetime import UTC, datetime

log = logging.getLogger(__name__)


class DailyArchive:
    def __init__(self, archive, source=None, symbols=None, interval=None):
        self.archive = archive
        self.source, self.symbols, self.interval = source, symbols, interval
        self.completed_day = None
        self.retry_at = 0

    def tick(self, now=None, max_objects=None):
        if max_objects is not None and max_objects < 1:
            raise ValueError("max_objects must be positive")
        now = now or datetime.now(UTC)
        now = now.astimezone(UTC)
        day = now.date().isoformat()
        if day == self.completed_day or now.timestamp() < self.retry_at:
            return None
        # An unavailable object store must not monopolize ordinary ingestion.
        self.retry_at = now.timestamp() + 60
        published, rows = 0, 0
        try:
            lookahead = max_objects + 1 if max_objects is not None else None
            groups = self.archive.eligible(
                self.source,
                self.symbols,
                self.interval,
                now=now,
                max_groups=lookahead,
            )
            selected = groups if max_objects is None else groups[:max_objects]
            for candles in selected:
                # Use the same validated snapshot/version-pruning contract as
                # the archive CLI, including approved legacy/native adoptions.
                self.archive.publish(candles, prune=True, require_current=True)
                published += 1
                rows += len(candles)
            more_groups = len(groups) > published
            if not more_groups:
                more_groups = bool(
                    self.archive.eligible(
                        self.source,
                        self.symbols,
                        self.interval,
                        now=now,
                        max_groups=1,
                    )
                )
            if more_groups:
                # Exact-version pruning deliberately retains concurrent writes.
                # Re-export them after cooldown rather than declaring completion.
                return {"day": day, "complete": False, "objects": published, "rows": rows}
        except Exception as exc:
            log.warning(
                "daily archive deferred after %s objects: %s", published, type(exc).__name__
            )
            return {"day": day, "complete": False, "objects": published, "rows": rows}
        self.completed_day = day
        log.info("daily archive day=%s objects=%s rows=%s", day, published, rows)
        return {"day": day, "complete": True, "objects": published, "rows": rows}
