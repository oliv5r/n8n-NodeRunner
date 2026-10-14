"""Duration parsing (mm:ss:ms) and a sub-millisecond scheduler."""

from __future__ import annotations

import re
import time

# mm:ss:ms -- minutes, seconds, milliseconds. The capture's 75298 ms is
# 01:15:298 in this notation, which is why the ms field is the last one.
_PATTERNS = [
    re.compile(r"^(?P<m>\d+):(?P<s>\d{1,2}):(?P<ms>\d{1,3})$"),
    re.compile(r"^(?P<m>\d+):(?P<s>\d{1,2})[.,](?P<ms>\d{1,3})$"),
    re.compile(r"^(?P<s>\d+)[.,](?P<ms>\d{1,3})$"),
    re.compile(r"^(?P<m>\d+):(?P<s>\d{1,2})$"),
]


class TimeFormatError(ValueError):
    pass


def parse_duration(text: str) -> int:
    """Parse 'mm:ss:ms' (or a few close variants) into milliseconds."""
    raw = text.strip()
    if not raw:
        raise TimeFormatError("empty input")

    for pattern in _PATTERNS:
        match = pattern.match(raw)
        if not match:
            continue
        parts = match.groupdict()
        minutes = int(parts.get("m") or 0)
        seconds = int(parts.get("s") or 0)
        ms_field = parts.get("ms")
        if ms_field is None:
            millis = 0
        else:
            # '5' means 500 ms, '50' means 500 ms, '500' means 500 ms -- the
            # field is a fraction of a second, not a count.
            millis = int(ms_field.ljust(3, "0"))
        # The 0-59 bound only applies when minutes are present; on its own a
        # seconds field is just a duration and may exceed 59.
        if parts.get("m") is not None and seconds > 59:
            raise TimeFormatError(f"seconds out of range: {seconds} (expected 0-59)")
        if millis > 999:
            raise TimeFormatError(f"milliseconds out of range: {millis}")
        total = (minutes * 60 + seconds) * 1000 + millis
        if total <= 0:
            raise TimeFormatError("duration must be greater than zero")
        return total

    raise TimeFormatError(
        f"could not parse {raw!r} -- expected mm:ss:ms, e.g. 01:15:298"
    )


def format_duration(ms: int) -> str:
    minutes, rest = divmod(ms, 60_000)
    seconds, millis = divmod(rest, 1000)
    return f"{minutes:02d}:{seconds:02d}:{millis:03d}"


def wait_until(deadline: float) -> float:
    """Block until perf_counter() reaches `deadline`; return the actual arrival.

    Sleeps for the bulk, then spins the final few milliseconds. The spin window
    is deliberately generous: time.sleep() overshoots by a millisecond or more on
    macOS, which would otherwise bias every attempt late. Measured over 60
    trials this lands within ~0.2 ms, at negligible CPU cost.
    """
    while True:
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            break
        if remaining > 0.005:
            time.sleep(remaining - 0.004)
        # else: spin the last few milliseconds
    return time.perf_counter()
