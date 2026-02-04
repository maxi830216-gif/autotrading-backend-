"""
Timezone utilities for KST (Korea Standard Time)
"""
from datetime import datetime, timezone, timedelta

# KST = UTC + 9 hours
KST = timezone(timedelta(hours=9))


def now_kst() -> datetime:
    """Return current time in KST timezone"""
    return datetime.now(KST)


def to_kst(dt: datetime) -> datetime:
    """Convert datetime to KST timezone"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        # Assume naive datetime is UTC
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(KST)
