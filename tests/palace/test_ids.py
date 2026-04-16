"""Tests for coord ID helpers."""
from __future__ import annotations

import pytest

from mybot.palace.ids import Tower, make_id, parse_id


def test_north_id_roundtrip():
    cid = make_id(Tower.NORTH, 2026, 107, 5, 7)
    assert cid == "N-2026-107-05-07"
    t, y, f, r, d = parse_id(cid)
    assert (t, y, f, r, d) == (Tower.NORTH, 2026, 107, 5, 7)


def test_south_id_padding():
    assert make_id(Tower.SOUTH, 2026, 1, 1, 1) == "S-2026-001-01-01"


def test_max_bounds():
    assert make_id(Tower.NORTH, 2026, 365, 20, 20) == "N-2026-365-20-20"


@pytest.mark.parametrize("args", [
    (2026, 0, 1, 1),
    (2026, 366, 1, 1),
    (2026, 100, 0, 1),
    (2026, 100, 21, 1),
    (2026, 100, 1, 0),
    (2026, 100, 1, 21),
])
def test_invalid_coords_raise(args):
    with pytest.raises(ValueError):
        make_id(Tower.NORTH, *args)


def test_parse_invalid_raises():
    with pytest.raises(ValueError):
        parse_id("garbage")
    with pytest.raises(ValueError):
        parse_id("X-2026-001-01-01")
    with pytest.raises(ValueError):
        parse_id("N-26-001-01-01")
