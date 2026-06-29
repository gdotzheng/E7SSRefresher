"""STEP 1 — Feasibility probe. Run this FIRST, with Epic Seven open.

It answers two questions that decide the whole run mode:
  1. Can we capture the window while it is NOT focused (behind other windows)?
  2. Does a background PostMessage click register in the game?

Usage:
  # Capture test only (safe): put E7 behind this terminal, then run
  python tools/probe.py

  # Also fire a test click at client coords (do this on a harmless button while watching E7)
  python tools/probe.py --click 400 300

Outputs probe_wgc.png and probe_printwindow.png next to this script for visual inspection.
"""

from __future__ import annotations

import os
import sys
import argparse
import time

import numpy as np
import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import window as W  # noqa: E402

OUT_DIR = os.path.dirname(os.path.abspath(__file__))


def _describe(img: np.ndarray) -> str:
    mean = float(img.mean())
    std = float(img.std())
    verdict = "LOOKS BLACK/BLANK" if mean < 5 or std < 3 else "looks like real content"
    return f"shape={img.shape} mean={mean:.1f} std={std:.1f} -> {verdict}"


def test_capture(gw: "W.GameWindow", backend: str) -> bool:
    print(f"\n[{backend}] capturing...")
    try:
        cap = W.make_capture(gw.hwnd, backend)
    except Exception as e:
        print(f"  backend init failed: {e!r}")
        return False
    try:
        img = cap.capture()
        out = os.path.join(OUT_DIR, f"probe_{backend}.png")
        cv2.imwrite(out, img)
        print(f"  saved {out}")
        print(f"  {_describe(img)}")
        return float(img.mean()) >= 5 and float(img.std()) >= 3
    except Exception as e:
        print(f"  capture failed: {e!r}")
        return False
    finally:
        cap.close()


def test_click(gw: "W.GameWindow", x: int, y: int):
    print(f"\n[input] sending background PostMessage click at client ({x},{y})")
    print("  watch Epic Seven now — did the button react?")
    inp = W.PostMessageInput(gw.hwnd)
    time.sleep(1.5)
    inp.click(x, y)
    print("  click sent.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--click", nargs=2, type=int, metavar=("X", "Y"),
                    help="also fire a background test click at client coords")
    args = ap.parse_args()

    gw = W.find_game_window()
    if gw is None:
        print("Epic Seven window not found. Is the game running?")
        sys.exit(1)

    print(f"Found window: hwnd={gw.hwnd} title={W.window_title(gw.hwnd)!r} "
          f"client={gw.width}x{gw.height}")

    wgc_ok = test_capture(gw, "wgc")
    pw_ok = test_capture(gw, "printwindow")

    if args.click:
        test_click(gw, args.click[0], args.click[1])

    print("\n================ RESULT ================")
    print(f"  WGC capture:          {'OK' if wgc_ok else 'FAILED/black'}")
    print(f"  PrintWindow capture:  {'OK' if pw_ok else 'FAILED/black'}")
    print("  Inspect probe_*.png to confirm the image really shows the game.")
    if args.click:
        print("  Background click: judge by what you saw in-game above.")
    print("\nDecide the mode in config.json:")
    print("  capture OK + click works  -> mode=background, capture=<the one that worked>")
    print("  capture OK + click ignored-> mode=hybrid  (real cursor only at click moments)")
    print("  capture FAILED everywhere -> mode=foreground (game must stay in front)")
    print("=======================================")


if __name__ == "__main__":
    main()
