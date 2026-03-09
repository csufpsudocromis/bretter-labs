from datetime import datetime, timezone


def utc_now() -> datetime:
    """Return a naive UTC datetime for DB fields stored without tzinfo."""
    return datetime.now(timezone.utc).replace(tzinfo=None)
