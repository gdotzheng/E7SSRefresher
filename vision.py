"""Vision layer: OpenCV template matching over captured frames.

Templates are PNG crops captured from the user's own client (see tools/snip.py). All
returned coordinates are client-relative pixel centers, ready to hand to an Input.click().
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass

import cv2
import numpy as np


def _res_dir() -> str:
    """Read-only resources: the PyInstaller bundle dir when frozen, else this file's dir."""
    return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))


TEMPLATE_DIR = os.path.join(_res_dir(), "templates")


@dataclass
class Match:
    x: int          # client-relative center x
    y: int          # client-relative center y
    score: float
    w: int
    h: int


_cache: dict[str, np.ndarray] = {}


def load_template(name: str) -> np.ndarray:
    """Load templates/<name>.png (BGR). Cached."""
    if name in _cache:
        return _cache[name]
    path = os.path.join(TEMPLATE_DIR, f"{name}.png")
    img = cv2.imread(path, cv2.IMREAD_COLOR)
    if img is None:
        raise FileNotFoundError(f"template not found: {path}")
    _cache[name] = img
    return img


def find(name: str, screen: np.ndarray, threshold: float = 0.85,
         scales: tuple[float, ...] = (1.0,)) -> Match | None:
    """Best single match of template `name` in `screen`, or None below threshold.

    `scales` lets us tolerate minor resolution differences; default is exact scale,
    which is the most reliable when templates come from the same client."""
    tmpl = load_template(name)
    best: Match | None = None
    for s in scales:
        t = tmpl if s == 1.0 else cv2.resize(tmpl, None, fx=s, fy=s,
                                             interpolation=cv2.INTER_AREA)
        th, tw = t.shape[:2]
        if th > screen.shape[0] or tw > screen.shape[1]:
            continue
        res = cv2.matchTemplate(screen, t, cv2.TM_CCOEFF_NORMED)
        _minv, maxv, _minl, maxl = cv2.minMaxLoc(res)
        if best is None or maxv > best.score:
            best = Match(x=maxl[0] + tw // 2, y=maxl[1] + th // 2,
                         score=float(maxv), w=tw, h=th)
    if best is not None and best.score >= threshold:
        return best
    return None


def find_all(name: str, screen: np.ndarray, threshold: float = 0.85,
             min_dist: int = 20) -> list[Match]:
    """All non-overlapping matches of `name` above threshold (for the multiple shop slots)."""
    tmpl = load_template(name)
    th, tw = tmpl.shape[:2]
    res = cv2.matchTemplate(screen, tmpl, cv2.TM_CCOEFF_NORMED)
    ys, xs = np.where(res >= threshold)
    cands = sorted(zip(xs.tolist(), ys.tolist()), key=lambda p: -res[p[1], p[0]])
    picked: list[Match] = []
    for x, y in cands:
        cx, cy = x + tw // 2, y + th // 2
        if all(abs(cx - m.x) > min_dist or abs(cy - m.y) > min_dist for m in picked):
            picked.append(Match(x=cx, y=cy, score=float(res[y, x]), w=tw, h=th))
    return picked


def present(name: str, screen: np.ndarray, threshold: float = 0.85) -> bool:
    return find(name, screen, threshold) is not None


def on_secret_shop_screen(screen: np.ndarray, threshold: float = 0.85) -> bool:
    """Guard: only act when the Secret Shop marker is visible."""
    return present("shop_marker", screen, threshold)


def annotate(screen: np.ndarray, matches: dict[str, list[Match]]) -> np.ndarray:
    """Draw boxes/labels for dry-run debugging."""
    out = screen.copy()
    for label, ms in matches.items():
        for m in ms:
            x0, y0 = m.x - m.w // 2, m.y - m.h // 2
            cv2.rectangle(out, (x0, y0), (x0 + m.w, y0 + m.h), (0, 255, 0), 2)
            cv2.putText(out, f"{label} {m.score:.2f}", (x0, max(0, y0 - 5)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
    return out
