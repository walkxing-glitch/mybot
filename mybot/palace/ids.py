"""Coordinate ID helpers.

Format: ``{N|S}-YYYY-FFF-RR-DD``
    - Tower letter: N (north raw conversations) or S (south summaries)
    - YYYY: four-digit year
    - FFF:  day-of-year (1..365)
    - RR:   room (1..20)
    - DD:   drawer (1..20)

Example: ``N-2026-107-05-07`` — 2026 year, 107th day, room 5, drawer 7.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Tuple


class Tower(str, Enum):
    NORTH = "N"
    SOUTH = "S"


_ID_RE = re.compile(r"^([NS])-(\d{4})-(\d{3})-(\d{2})-(\d{2})$")


def make_id(tower: Tower, year: int, floor: int, room: int, drawer: int) -> str:
    if not (1 <= floor <= 365):
        raise ValueError(f"floor out of range [1..365]: {floor}")
    if not (1 <= room <= 20):
        raise ValueError(f"room out of range [1..20]: {room}")
    if not (1 <= drawer <= 20):
        raise ValueError(f"drawer out of range [1..20]: {drawer}")
    return f"{tower.value}-{year:04d}-{floor:03d}-{room:02d}-{drawer:02d}"


def parse_id(cid: str) -> Tuple[Tower, int, int, int, int]:
    m = _ID_RE.match(cid)
    if not m:
        raise ValueError(f"invalid coord id: {cid!r}")
    t, y, f, r, d = m.groups()
    return Tower(t), int(y), int(f), int(r), int(d)
