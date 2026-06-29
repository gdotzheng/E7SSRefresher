"""Capture the live Epic Seven client so you can crop template PNGs from it.

The bot matches images at YOUR resolution, so templates must come from your own client.

Workflow:
  1. Launch E7, go to the Secret Shop (with items visible).
  2. Run:  python tools/snip.py --backend wgc        (or --backend printwindow)
  3. It saves tools/snapshot.png — the full client area.
  4. Open snapshot.png in any image editor and crop these into ../templates/ :
        shop_marker.png      a unique bit of the Secret Shop UI (title/tab)
        refresh_button.png   the Refresh button
        refresh_confirm.png  the "Use skystones?" confirm button
        buy_button.png       the Buy button on an item's purchase popup
        buy_confirm.png      the purchase confirm button
        covenant_bookmark.png  the Covenant Bookmark item icon as shown in a slot
        mystic_medal.png       the Mystic Medal/Bookmark item icon as shown in a slot
     Crop tightly; avoid changing/animated regions (counts, timers, sparkles).
"""

from __future__ import annotations

import os
import sys
import argparse

import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import window as W  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "snapshot.png")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", default="wgc", choices=["wgc", "printwindow"])
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()

    gw = W.find_game_window()
    if gw is None:
        print("Epic Seven window not found. Is the game running?")
        sys.exit(1)

    cap = W.make_capture(gw.hwnd, args.backend)
    try:
        img = cap.capture()
    finally:
        cap.close()

    cv2.imwrite(args.out, img)
    print(f"Saved {args.out}  ({img.shape[1]}x{img.shape[0]})")
    print("Now crop the templates listed in this file's docstring into ../templates/")


if __name__ == "__main__":
    main()
