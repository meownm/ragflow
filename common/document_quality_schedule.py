"""Pure schedule policy shared by the scheduler and its external watchdog."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

MOSCOW = ZoneInfo("Europe/Moscow")
NIGHTLY_HOUR = 2
MONTHLY_HOUR = 3
NIGHTLY_GRACE = timedelta(minutes=15)
NIGHTLY_MAX_DURATION = timedelta(hours=12)


def expected_night_slot(now: datetime) -> datetime:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Nightly time must include a timezone")
    local = now.astimezone(MOSCOW)
    slot = local.replace(hour=NIGHTLY_HOUR, minute=0, second=0, microsecond=0)
    return slot - timedelta(days=1) if local < slot + NIGHTLY_GRACE else slot
