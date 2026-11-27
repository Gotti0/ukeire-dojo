"""누적 방문자/풀이 일별 집계와 손그림 SVG 차트 생성기를 검증한다."""

from __future__ import annotations

import sqlite3
import xml.etree.ElementTree as ET
from contextlib import closing
from datetime import date, timedelta

from ukeire_dojo.chart import render_history_chart_svg
from ukeire_dojo.practice import Practice


def test_daily_history_empty_single_and_sparse_days(tmp_path):
    db_path = tmp_path / "practice.sqlite"
    practice = Practice(db_path)

    today = date(2026, 11, 27)
    empty = practice.daily_history(today=today)
    assert len(empty) == 7
    assert empty[0] == {"date": "2026-11-21", "visitors": 0, "answered": 0}
    assert empty[-1] == {"date": "2026-11-27", "visitors": 0, "answered": 0}

    with closing(sqlite3.connect(db_path)) as db, db:
        db.execute(
            "INSERT INTO visitors (profile, first_seen, last_seen) VALUES (?, ?, ?)",
            ("v1", "2026-11-25 09:00:00", "2026-11-25 09:00:00"),
        )
        db.execute(
            "INSERT INTO attempts (profile, attempt, problem_id, kind, tile, loss, answered_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("v1", "a1", 1, "closed_hand", "5m", 0.0, "2026-11-25 09:01:00"),
        )

    single = practice.daily_history(today=date(2026, 11, 25))
    assert single == [
        {"date": "2026-11-24", "visitors": 0, "answered": 0},
        {"date": "2026-11-25", "visitors": 1, "answered": 1},
    ]

    with closing(sqlite3.connect(db_path)) as db, db:
        db.execute(
            "INSERT INTO visitors (profile, first_seen, last_seen) VALUES (?, ?, ?)",
            ("v2", "2026-11-27 12:00:00", "2026-11-27 12:00:00"),
        )
        db.execute(
            "INSERT INTO attempts (profile, attempt, problem_id, kind, tile, loss, answered_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("v2", "a2", 2, "open_hand", "1p", 0.02, "2026-11-27 12:05:00"),
        )
        db.execute(
            "INSERT INTO attempts (profile, attempt, problem_id, kind, tile, loss, answered_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("v2", "a3", 3, "opp_riichi", "9s", 0.0, "2026-11-27 12:06:00"),
        )

    sparse = practice.daily_history(today=today)
    assert sparse == [
        {"date": "2026-11-24", "visitors": 0, "answered": 0},
        {"date": "2026-11-25", "visitors": 1, "answered": 1},
        {"date": "2026-11-26", "visitors": 1, "answered": 1},
        {"date": "2026-11-27", "visitors": 2, "answered": 3},
    ]


def test_render_history_chart_svg_variants_and_safety():
    samples = [
        [],
        [{"date": "2026-11-27", "visitors": 1, "answered": 2}],
        [
            {"date": "2026-11-24", "visitors": 0, "answered": 0},
            {"date": "2026-11-25", "visitors": 3, "answered": 12},
            {"date": "2026-11-26", "visitors": 3, "answered": 12},
            {"date": "2026-11-27", "visitors": 8, "answered": 45},
        ],
        [
            {
                "date": (date(2026, 1, 1) + timedelta(days=i)).isoformat(),
                "visitors": i * 2,
                "answered": i * 7,
            }
            for i in range(200)
        ],
    ]

    for history in samples:
        svg_bytes = render_history_chart_svg(history)
        assert svg_bytes == render_history_chart_svg(history)
        text = svg_bytes.decode("utf-8")
        assert "<script" not in text.lower()
        assert "<foreignobject" not in text.lower()
        without_xmlns = text.lower().replace('xmlns="http://www.w3.org/2000/svg"', "")
        assert "http://" not in without_xmlns
        assert "https://" not in without_xmlns
        assert "nan" not in text.lower()
        root = ET.fromstring(text)
        assert root.tag.endswith("svg")
        assert "Visitor History" in text
        assert "#ffffff" in text

    dark_svg = render_history_chart_svg(samples[2], theme="dark").decode("utf-8")
    assert "#0d1117" in dark_svg
    ET.fromstring(dark_svg)

    solved_svg = render_history_chart_svg(samples[2], metric="solved").decode("utf-8")
    assert "Solved History" in solved_svg
    assert "45" in solved_svg
    ET.fromstring(solved_svg)
