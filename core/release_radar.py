"""Date filtering helpers for the followed-artists release feed."""
from datetime import date, timedelta


def is_recent_release(album, days=90, today=None):
    """Return True only when an album has a valid release date in the recent window."""
    if not isinstance(album, dict):
        return False
    raw = str(album.get("release_date") or "").strip()
    if len(raw) < 10:
        return False
    try:
        released = date.fromisoformat(raw[:10])
    except ValueError:
        return False
    current = today or date.today()
    return current - timedelta(days=max(1, int(days))) <= released <= current