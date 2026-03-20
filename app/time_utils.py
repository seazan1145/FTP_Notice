from __future__ import annotations

from datetime import datetime, timezone


def to_utc_isoformat(value: datetime) -> str:
    dt = value
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.isoformat()


def parse_ftp_datetime(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None

    if isinstance(value, datetime):
        return value

    text = value.strip()
    if not text:
        return None

    iso_candidate = text.replace("Z", "+00:00") if text.endswith("Z") else text
    try:
        return datetime.fromisoformat(iso_candidate)
    except ValueError:
        pass

    for pattern in ("%Y%m%d%H%M%S.%f", "%Y%m%d%H%M%S"):
        try:
            return datetime.strptime(text, pattern)
        except ValueError:
            continue
    return None


def normalize_ftp_datetime(value: str | datetime | None) -> str:
    parsed = parse_ftp_datetime(value)
    if parsed is None:
        return datetime.now(timezone.utc).isoformat()
    return to_utc_isoformat(parsed)
