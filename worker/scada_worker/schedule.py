"""Schedule intervals: [{"days": [ISO weekdays], "start": "HH:MM", "end": "HH:MM"}].

end < start means the interval crosses midnight and belongs to the day it starts on;
"24:00" is allowed as the end of the day.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def in_schedule(intervals: list[dict], timezone: str, at: datetime) -> bool:
    local = at.astimezone(ZoneInfo(timezone))
    now = local.hour * 60 + local.minute
    today = local.isoweekday()
    yesterday = (local - timedelta(days=1)).isoweekday()
    for iv in intervals:
        start, end, days = _minutes(iv["start"]), _minutes(iv["end"]), iv["days"]
        if start <= end:
            if today in days and start <= now < end:
                return True
        elif (today in days and now >= start) or (yesterday in days and now < end):
            return True
    return False
