"""GitHub README용 손그림(Star History / xkcd) 스타일 누적 추이 SVG 차트 생성기."""

from __future__ import annotations

import math
import random
from datetime import date, datetime, timedelta, timezone
from xml.sax.saxutils import escape

WIDTH = 800
HEIGHT = 480
LEFT = 88.0
RIGHT = 752.0
TOP = 94.0
BOTTOM = 404.0

FONT_FAMILY = "'Comic Sans MS', 'Chalkboard SE', 'Bradley Hand', 'Segoe Print', cursive, sans-serif"
SHORT_MONTHS = ("", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

THEMES = {
    "light": {
        "bg": "#ffffff",
        "border": "#d0d7de",
        "ink": "#1f2328",
        "muted": "#59636e",
        "grid": "#8c959f",
        "legend_bg": "#ffffff",
        "visitors_accent": "#dd4528",
        "solved_accent": "#2b62c4",
    },
    "dark": {
        "bg": "#0d1117",
        "border": "#30363d",
        "ink": "#e6edf3",
        "muted": "#8b949e",
        "grid": "#484f58",
        "legend_bg": "#161b22",
        "visitors_accent": "#ff7b5c",
        "solved_accent": "#58a6ff",
    },
}


def _downsample(history: list[dict[str, int | str]], max_points: int = 90) -> list[dict[str, int | str]]:
    if len(history) <= max_points:
        return list(history)
    n = len(history)
    indices = sorted({round(i * (n - 1) / (max_points - 1)) for i in range(max_points)})
    if indices[-1] != n - 1:
        indices[-1] = n - 1
    return [history[i] for i in indices]


def _nice_y_ticks(max_val: int) -> tuple[int, list[int]]:
    if max_val <= 4:
        return 4, [0, 1, 2, 3, 4]
    raw_step = max_val / 5.0
    exp = math.floor(math.log10(raw_step))
    magnitude = 10 ** exp
    step = magnitude
    for base in (1, 2, 5, 10):
        candidate = int(base * magnitude)
        if candidate >= 1 and math.ceil(max_val / candidate) <= 6:
            step = candidate
            break
    intervals = max(4, math.ceil(max_val / step))
    y_max = step * intervals
    return y_max, list(range(0, y_max + 1, step))


def _sketch_stroke(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    rng: random.Random,
    roughness: float = 1.3,
) -> str:
    dx = x2 - x1
    dy = y2 - y1
    length = math.hypot(dx, dy)
    if length < 1e-6:
        return f"M {x1:.1f} {y1:.1f}"
    segments = max(1, min(16, int(math.ceil(length / 32.0))))
    nx = -dy / length
    ny = dx / length
    parts = [f"M {x1 + rng.uniform(-roughness * 0.4, roughness * 0.4):.1f} {y1 + rng.uniform(-roughness * 0.4, roughness * 0.4):.1f}"]
    for i in range(segments):
        t_mid = (i + 0.5) / segments
        t_end = (i + 1.0) / segments
        mid_off = rng.uniform(-roughness, roughness)
        cx = x1 + dx * t_mid + nx * mid_off
        cy = y1 + dy * t_mid + ny * mid_off
        end_scale = 0.4 if i == segments - 1 else 0.75
        end_off = rng.uniform(-roughness * end_scale, roughness * end_scale)
        ex = x1 + dx * t_end + nx * end_off
        ey = y1 + dy * t_end + ny * end_off
        parts.append(f"Q {cx:.1f} {cy:.1f} {ex:.1f} {ey:.1f}")
    return " ".join(parts)


def _sketch_line_paths(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    rng: random.Random,
    roughness: float = 1.3,
    double: bool = True,
) -> list[str]:
    paths = [_sketch_stroke(x1, y1, x2, y2, rng, roughness=roughness)]
    if double:
        paths.append(_sketch_stroke(x1, y1, x2, y2, rng, roughness=roughness * 1.15))
    return paths


def _sketch_rect_paths(x: float, y: float, w: float, h: float, rng: random.Random, roughness: float = 1.1) -> list[str]:
    edges = (
        (x, y, x + w, y),
        (x + w, y, x + w, y + h),
        (x + w, y + h, x, y + h),
        (x, y + h, x, y),
    )
    paths: list[str] = []
    for x1, y1, x2, y2 in edges:
        paths.extend(_sketch_line_paths(x1, y1, x2, y2, rng, roughness=roughness, double=True))
    return paths


def _monotone_tangents(xs: list[float], ys: list[float]) -> list[float]:
    n = len(xs)
    if n < 2:
        return [0.0] * n
    dxs = [xs[i + 1] - xs[i] for i in range(n - 1)]
    slopes = [(ys[i + 1] - ys[i]) / dxs[i] if dxs[i] > 0 else 0.0 for i in range(n - 1)]
    tangents = [0.0] * n
    tangents[0] = slopes[0]
    tangents[-1] = slopes[-1]
    for i in range(1, n - 1):
        m0, m1 = slopes[i - 1], slopes[i]
        if m0 * m1 <= 0:
            tangents[i] = 0.0
        else:
            w1 = 2.0 * dxs[i] + dxs[i - 1]
            w2 = dxs[i] + 2.0 * dxs[i - 1]
            tangents[i] = (w1 + w2) / (w1 / m0 + w2 / m1)
    for i in range(n - 1):
        m = slopes[i]
        if abs(m) < 1e-12:
            tangents[i] = 0.0
            tangents[i + 1] = 0.0
        else:
            a = tangents[i] / m
            b = tangents[i + 1] / m
            norm2 = a * a + b * b
            if norm2 > 9.0:
                tau = 3.0 / math.sqrt(norm2)
                tangents[i] = tau * a * m
                tangents[i + 1] = tau * b * m
    return tangents


def _monotone_cubic_path(
    coords: list[tuple[float, float]],
    rng: random.Random,
    roughness: float = 0.9,
) -> tuple[str, str]:
    xs = [p[0] for p in coords]
    ys = [p[1] for p in coords]
    tangents = _monotone_tangents(xs, ys)

    main_parts = [f"M {xs[0]:.1f} {ys[0]:.1f}"]
    wobble_parts = [
        f"M {xs[0] + rng.uniform(-roughness, roughness):.1f} {min(BOTTOM, ys[0] + rng.uniform(-roughness, roughness)):.1f}"
    ]
    for i in range(len(coords) - 1):
        dx = xs[i + 1] - xs[i]
        c1x = xs[i] + dx / 3.0
        c1y = max(TOP - 2.0, min(BOTTOM, ys[i] + tangents[i] * dx / 3.0))
        c2x = xs[i + 1] - dx / 3.0
        c2y = max(TOP - 2.0, min(BOTTOM, ys[i + 1] - tangents[i + 1] * dx / 3.0))
        main_parts.append(f"C {c1x:.1f} {c1y:.1f} {c2x:.1f} {c2y:.1f} {xs[i + 1]:.1f} {ys[i + 1]:.1f}")

        wc1x = c1x + rng.uniform(-roughness, roughness)
        wc1y = min(BOTTOM, c1y + rng.uniform(-roughness, roughness))
        wc2x = c2x + rng.uniform(-roughness, roughness)
        wc2y = min(BOTTOM, c2y + rng.uniform(-roughness, roughness))
        wex = xs[i + 1] + rng.uniform(-roughness * 0.7, roughness * 0.7)
        wey = min(BOTTOM, ys[i + 1] + rng.uniform(-roughness * 0.7, roughness * 0.7))
        wobble_parts.append(f"C {wc1x:.1f} {wc1y:.1f} {wc2x:.1f} {wc2y:.1f} {wex:.1f} {wey:.1f}")

    return " ".join(main_parts), " ".join(wobble_parts)


def _format_date_label(raw: str, long_span: bool) -> str:
    try:
        parsed = date.fromisoformat(raw[:10])
    except ValueError:
        return raw[:10]
    if long_span:
        return parsed.strftime("%Y-%m")
    return f"{SHORT_MONTHS[parsed.month]} {parsed.day}"


def _pick_x_indices(count: int, max_ticks: int = 6) -> list[int]:
    if count <= max_ticks:
        return list(range(count))
    return sorted({round(i * (count - 1) / (max_ticks - 1)) for i in range(max_ticks)})


def _sketch_circle_path(cx: float, cy: float, r: float, rng: random.Random) -> str:
    pts: list[tuple[float, float]] = []
    steps = 12
    for i in range(steps):
        angle = 2.0 * math.pi * i / steps
        rr = r + rng.uniform(-0.7, 0.7)
        pts.append((cx + rr * math.cos(angle), cy + rr * math.sin(angle)))
    parts = [f"M {pts[0][0]:.1f} {pts[0][1]:.1f}"]
    for i in range(steps):
        p0 = pts[i]
        p1 = pts[(i + 1) % steps]
        mx = (p0[0] + p1[0]) / 2.0
        my = (p0[1] + p1[1]) / 2.0
        parts.append(f"Q {p0[0]:.1f} {p0[1]:.1f} {mx:.1f} {my:.1f}")
    parts.append("Z")
    return " ".join(parts)


def render_history_chart_svg(
    history: list[dict[str, int | str]],
    *,
    metric: str = "visitors",
    theme: str = "light",
    totals: dict[str, int] | None = None,
) -> bytes:
    """날짜별 누적 데이터를 손그림 스타일(Star History / xkcd) X/Y 선 그래프 SVG로 렌더링한다."""
    if metric == "solved":
        series_key = "answered"
        title = "Solved History"
        y_label = "Solved"
        accent_key = "solved_accent"
    else:
        series_key = "visitors"
        title = "Visitor History"
        y_label = "Visitors"
        accent_key = "visitors_accent"

    palette = THEMES["dark"] if theme == "dark" else THEMES["light"]
    accent = palette[accent_key]

    if not history:
        today = datetime.now(timezone.utc).date()
        points = [
            {"date": (today - timedelta(days=1)).isoformat(), "visitors": 0, "answered": 0},
            {"date": today.isoformat(), "visitors": 0, "answered": 0},
        ]
    elif len(history) == 1:
        only = history[0]
        try:
            prev_day = (date.fromisoformat(str(only["date"])[:10]) - timedelta(days=1)).isoformat()
        except ValueError:
            prev_day = str(only["date"])
        points = [{"date": prev_day, "visitors": 0, "answered": 0}, dict(only)]
    else:
        points = _downsample(history, max_points=90)

    last_point = points[-1]
    total_visitors = int((totals or {}).get("visitors", last_point.get("visitors", 0)))
    total_answered = int((totals or {}).get("answered", last_point.get("answered", 0)))
    visitor_unit = "visitor" if total_visitors == 1 else "visitors"
    subtitle = f"ukeire-dojo.me · {total_visitors:,} {visitor_unit} · {total_answered:,} solved"

    values = [max(0, int(p.get(series_key, 0))) for p in points]
    y_max, y_ticks = _nice_y_ticks(max(values))

    n = len(points)
    plot_w = RIGHT - LEFT
    plot_h = BOTTOM - TOP
    coords = [
        (
            LEFT + (i / (n - 1)) * plot_w,
            BOTTOM - (values[i] / y_max) * plot_h,
        )
        for i in range(n)
    ]

    try:
        d_first = date.fromisoformat(str(points[0]["date"])[:10])
        d_last = date.fromisoformat(str(points[-1]["date"])[:10])
        long_span = (d_last - d_first).days > 180
    except ValueError:
        long_span = False

    rng = random.Random(20261127)
    elements: list[str] = []

    # 카드 배경
    elements.append(
        f'<rect x="0.5" y="0.5" width="{WIDTH - 1}" height="{HEIGHT - 1}" rx="12" '
        f'fill="{palette["bg"]}" stroke="{palette["border"]}" stroke-width="1"/>'
    )

    # 제목과 요약
    elements.append(
        f'<text x="{WIDTH / 2:.1f}" y="46" text-anchor="middle" font-size="24" font-weight="700" '
        f'fill="{palette["ink"]}">{escape(title)}</text>'
    )
    elements.append(
        f'<text x="{WIDTH / 2:.1f}" y="70" text-anchor="middle" font-size="13" '
        f'fill="{palette["muted"]}">{escape(subtitle)}</text>'
    )

    # 가로 격자선과 Y축 눈금
    for tick in y_ticks:
        ty = BOTTOM - (tick / y_max) * plot_h
        if tick > 0:
            for d in _sketch_line_paths(LEFT, ty, RIGHT, ty, rng, roughness=0.9, double=False):
                elements.append(
                    f'<path d="{d}" fill="none" stroke="{palette["grid"]}" stroke-width="1" '
                    f'stroke-dasharray="4 4" opacity="0.22"/>'
                )
        for d in _sketch_line_paths(LEFT - 6, ty, LEFT, ty, rng, roughness=0.5, double=False):
            elements.append(f'<path d="{d}" fill="none" stroke="{palette["ink"]}" stroke-width="1.6"/>')
        elements.append(
            f'<text x="{LEFT - 12:.1f}" y="{ty + 4:.1f}" text-anchor="end" font-size="12" '
            f'fill="{palette["ink"]}">{tick:,}</text>'
        )

    # X축 눈금과 날짜 라벨
    for idx in _pick_x_indices(n):
        tx = coords[idx][0]
        label = escape(_format_date_label(str(points[idx]["date"]), long_span))
        for d in _sketch_line_paths(tx, BOTTOM, tx, BOTTOM + 6, rng, roughness=0.5, double=False):
            elements.append(f'<path d="{d}" fill="none" stroke="{palette["ink"]}" stroke-width="1.6"/>')
        elements.append(
            f'<text x="{tx:.1f}" y="{BOTTOM + 22:.1f}" text-anchor="middle" font-size="12" '
            f'fill="{palette["ink"]}">{label}</text>'
        )

    # 손그림 영역 채움 및 추이 곡선
    main_curve, wobble_curve = _monotone_cubic_path(coords, rng, roughness=0.95)
    area_path = f"{main_curve} L {coords[-1][0]:.1f} {BOTTOM:.1f} L {coords[0][0]:.1f} {BOTTOM:.1f} Z"
    elements.append(f'<path d="{area_path}" fill="{accent}" opacity="0.09"/>')
    elements.append(
        f'<path d="{wobble_curve}" fill="none" stroke="{accent}" stroke-width="2.2" '
        f'stroke-linecap="round" stroke-linejoin="round" opacity="0.45"/>'
    )
    elements.append(
        f'<path d="{main_curve}" fill="none" stroke="{accent}" stroke-width="2.8" '
        f'stroke-linecap="round" stroke-linejoin="round"/>'
    )

    # 손그림 X/Y 축과 화살표
    for d in _sketch_line_paths(LEFT, BOTTOM + 6, LEFT, TOP - 10, rng, roughness=1.2, double=True):
        elements.append(
            f'<path d="{d}" fill="none" stroke="{palette["ink"]}" stroke-width="2" stroke-linecap="round"/>'
        )
    for d in (
        _sketch_stroke(LEFT - 5, TOP - 2, LEFT, TOP - 12, rng, roughness=0.5),
        _sketch_stroke(LEFT + 5, TOP - 2, LEFT, TOP - 12, rng, roughness=0.5),
    ):
        elements.append(
            f'<path d="{d}" fill="none" stroke="{palette["ink"]}" stroke-width="2" stroke-linecap="round"/>'
        )

    for d in _sketch_line_paths(LEFT - 6, BOTTOM, RIGHT + 12, BOTTOM, rng, roughness=1.2, double=True):
        elements.append(
            f'<path d="{d}" fill="none" stroke="{palette["ink"]}" stroke-width="2" stroke-linecap="round"/>'
        )
    for d in (
        _sketch_stroke(RIGHT + 4, BOTTOM - 5, RIGHT + 14, BOTTOM, rng, roughness=0.5),
        _sketch_stroke(RIGHT + 4, BOTTOM + 5, RIGHT + 14, BOTTOM, rng, roughness=0.5),
    ):
        elements.append(
            f'<path d="{d}" fill="none" stroke="{palette["ink"]}" stroke-width="2" stroke-linecap="round"/>'
        )

    # 축 제목
    elements.append(
        f'<text x="{(LEFT + RIGHT) / 2:.1f}" y="{BOTTOM + 48:.1f}" text-anchor="middle" font-size="14" '
        f'font-weight="600" fill="{palette["ink"]}">Date</text>'
    )
    elements.append(
        f'<text x="28" y="{(TOP + BOTTOM) / 2:.1f}" text-anchor="middle" font-size="14" font-weight="600" '
        f'fill="{palette["ink"]}" transform="rotate(-90 28 {(TOP + BOTTOM) / 2:.1f})">{escape(y_label)}</text>'
    )

    # 최신 지점 마커 및 수치 라벨
    end_x, end_y = coords[-1]
    end_val = values[-1]
    circle_d = _sketch_circle_path(end_x, end_y, 5.2, rng)
    elements.append(
        f'<path d="{circle_d}" fill="{palette["bg"]}" stroke="{accent}" stroke-width="2.4"/>'
    )
    label_x = max(LEFT + 12.0, min(RIGHT - 10.0, end_x - 8.0))
    label_y = max(TOP + 18.0, min(BOTTOM - 14.0, end_y - 12.0))
    elements.append(
        f'<text x="{label_x:.1f}" y="{label_y:.1f}" text-anchor="end" font-size="14" font-weight="700" '
        f'fill="{accent}">{end_val:,}</text>'
    )

    # 좌상단 Star-History 스타일 범례 박스
    lx, ly, lw, lh = 108.0, 108.0, 206.0, 36.0
    elements.append(
        f'<rect x="{lx:.1f}" y="{ly:.1f}" width="{lw:.1f}" height="{lh:.1f}" rx="4" '
        f'fill="{palette["legend_bg"]}" opacity="0.92"/>'
    )
    for d in _sketch_rect_paths(lx, ly, lw, lh, rng, roughness=0.95):
        elements.append(
            f'<path d="{d}" fill="none" stroke="{palette["ink"]}" stroke-width="1.4" stroke-linecap="round"/>'
        )
    for d in _sketch_line_paths(lx + 12, ly + 18, lx + 36, ly + 18, rng, roughness=0.7, double=True):
        elements.append(
            f'<path d="{d}" fill="none" stroke="{accent}" stroke-width="2.5" stroke-linecap="round"/>'
        )
    swatch_dot = _sketch_circle_path(lx + 24, ly + 18, 3.2, rng)
    elements.append(f'<path d="{swatch_dot}" fill="{accent}" stroke="{accent}" stroke-width="1"/>')
    elements.append(
        f'<text x="{lx + 46:.1f}" y="{ly + 23:.1f}" font-size="13" font-weight="600" '
        f'fill="{palette["ink"]}">Gotti0/ukeire-dojo</text>'
    )

    # 우하단 워터마크
    elements.append(
        f'<text x="{RIGHT:.1f}" y="462" text-anchor="end" font-size="11" '
        f'fill="{palette["muted"]}">ukeire-dojo.me</text>'
    )

    aria_label = escape(f"{title} ({total_visitors:,} {visitor_unit}, {total_answered:,} solved)")
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" '
        f'viewBox="0 0 {WIDTH} {HEIGHT}" role="img" aria-label="{aria_label}">'
        f"<title>{aria_label}</title>"
        f'<g font-family="{FONT_FAMILY}">'
        f'{"".join(elements)}'
        f"</g>"
        f"</svg>"
    )
    return svg.encode("utf-8")
