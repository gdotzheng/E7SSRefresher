"""Epic Seven Secret Shop auto-refresher.

Loop: (optionally) buy target items -> refresh -> repeat, until the skystone budget for
refreshes is spent. Reads the screen by capture, matches buttons/items with templates,
and clicks via the configured input backend.

Usage:
  python refresher.py                 # run with config.json
  python refresher.py --dry-run       # detect & annotate only, no clicks (saves dryrun.png)
  python refresher.py --budget 12     # override skystone budget

Press the abort hotkey (default F12) at any time to stop.
"""

from __future__ import annotations

import os
import sys
import json
import time
import argparse
import logging

import cv2

import window as W
import vision as V

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("e7ss")

_abort = False


def _install_abort(hotkey: str):
    global _abort
    try:
        import keyboard
        keyboard.add_hotkey(hotkey, _set_abort)
        log.info("Abort hotkey armed: press %s to stop.", hotkey.upper())
    except Exception as e:  # keyboard often needs admin; degrade gracefully
        log.warning("Could not arm abort hotkey (%s). Use Ctrl+C to stop.", e)


def _set_abort():
    global _abort
    _abort = True
    log.warning("Abort requested — stopping after current step.")


def request_abort():
    """Public hook (used by the GUI) to stop the run loop."""
    _set_abort()


def reset_abort():
    """Public hook (used by the GUI) to re-arm before a new run."""
    global _abort
    _abort = False


def load_config(path: str = CONFIG_PATH) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# A Secret Shop refresh always costs 3 skystones.
REFRESH_COST = 3


# --------------------------------------------------------------------------- helpers
class Bot:
    def __init__(self, cfg: dict, gw: "W.GameWindow"):
        self.cfg = cfg
        self.gw = gw
        self.cap = W.make_capture(gw.hwnd, cfg["capture_backend"])
        self.inp = W.make_input(gw.hwnd, cfg["mode"])
        self.th = cfg["match_threshold"]
        self.scales = tuple(cfg.get("scales", [1.0]))
        self.d = cfg["delays"]
        self.scroll_cfg = cfg.get("scroll", {
            "point": [750, 350], "step_notches": 5, "max_pages": 6,
            "list_region_x": 360, "change_threshold": 5.0,
        })
        # live stats (read by the GUI during a run)
        self.stats = {"refreshes": 0, "skystones_spent": 0, "bought": {}}
        self.started_at = None

    def grab(self):
        return self.cap.capture()

    def fit_window(self):
        """Resize the game to the resolution the templates were captured at, so matching is
        reliable no matter how the window was left. Controlled by config auto_resize."""
        if not self.cfg.get("auto_resize", False):
            return
        w, h = self.cfg.get("target_window", [1108, 623])
        cw, ch = W.set_client_size(self.gw.hwnd, int(w), int(h))
        if (cw, ch) != (int(w), int(h)):
            log.warning("Wanted client %sx%s but got %sx%s — templates may not match.",
                        w, h, cw, ch)
        else:
            log.info("Game window sized to %sx%s for template matching.", cw, ch)

    def click_match(self, m: "V.Match", wait: float):
        self.inp.click(m.x, m.y)
        time.sleep(wait)

    def find(self, name, screen):
        return V.find(name, screen, self.th, self.scales)

    def find_all(self, name, screen):
        return V.find_all(name, screen, self.th)

    def save_debug(self, screen, tag: str):
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"debug_{tag}.png")
        cv2.imwrite(path, screen)
        log.info("Saved %s", path)

    # ---- buy pass --------------------------------------------------------
    def _find_items(self, target, screen):
        try:
            return self.find_all(target, screen)
        except FileNotFoundError:
            log.warning("template '%s' not captured yet - skipping it.", target)
            return []

    def _list_change(self, a, b) -> float:
        """Mean diff of just the item-list region (excludes the animated character art),
        so scrolling is detectable without animation noise."""
        x0 = int(self.scroll_cfg["list_region_x"])
        return float(cv2.absdiff(a[:, x0:], b[:, x0:]).mean())

    def scroll_to_top(self):
        px, py = self.scroll_cfg["point"]
        self.inp.scroll(40, px, py)  # up clamps at the top
        time.sleep(self.d["loop_idle"] + 0.2)

    def buy_targets(self):
        """Scroll the (short) shop list from top to bottom, buying enabled targets on each
        view. The list only scrolls a little, so a few pages cover it."""
        sc = self.scroll_cfg
        px, py = sc["point"]
        self.dismiss_dialog()  # start from a clean shop (no leftover/manual popup)
        self.scroll_to_top()
        for page in range(int(sc["max_pages"])):
            if _abort:
                return
            screen = self.grab()
            self._buy_visible(screen)
            before = self.grab()
            self.inp.scroll(-int(sc["step_notches"]), px, py)
            time.sleep(self.d["loop_idle"] + 0.3)
            after = self.grab()
            if self._list_change(before, after) < float(sc["change_threshold"]):
                break  # nothing new scrolled into view -> bottom reached
        self.scroll_to_top()

    def _buy_visible(self, screen):
        for target in self.cfg["buy_targets"]:
            if _abort:
                return
            items = self._find_items(target, screen)
            if not items:
                continue
            log.info("Found %d x %s in view", len(items), target)
            for m in items:
                if _abort:
                    return
                self._buy_one(m, target)

    def dismiss_dialog(self, tries: int = 4) -> bool:
        """Close any open Cancel/Confirm or Buy popup (click Cancel). Loops because a popup
        may still be animating open when first clicked, and dialogs can stack — keep going
        until none is detected. Stops the bot from acting *through* a stray popup."""
        dismissed = False
        for _ in range(tries):
            c = self.find("cancel_button", self.grab())
            if not c:
                break
            log.info("  dismissing an open dialog")
            self.click_match(c, self.d["after_click"])
            dismissed = True
        return dismissed

    def _buy_one(self, item_match: "V.Match", target: str):
        # Click the Buy button on the SAME row as the target item (buy buttons share an x,
        # so match by row/y) to open its purchase popup, then confirm — but ONLY after
        # verifying a real purchase popup actually opened, so we never buy the wrong item.
        self.dismiss_dialog()
        screen = self.grab()
        buys = self.find_all("buy_button", screen)
        if not buys:
            log.info("  no Buy button visible for %s - skipping", target)
            return
        row_buy = min(buys, key=lambda b: abs(b.y - item_match.y))
        if abs(row_buy.y - item_match.y) > 40:
            log.info("  couldn't match a Buy button to the %s row - skipping", target)
            return
        self.click_match(row_buy, self.d["after_click"])  # should open the purchase popup
        screen = self.grab()
        # A real purchase popup has a Cancel button. If it's absent, the click didn't open a
        # popup (or hit the wrong spot) — do NOT click buy_confirm, because it can match a
        # shop "Buy" button and buy the wrong item. Bail safely instead.
        if not self.find("cancel_button", screen):
            log.info("  purchase popup didn't open for %s - skipping", target)
            return
        confirm = self.find("buy_confirm", screen)
        if not confirm:
            log.info("  Buy button missing in popup for %s - cancelling", target)
            self.dismiss_dialog()
            return
        self.click_match(confirm, self.d["after_buy"])
        self.stats["bought"][target] = self.stats["bought"].get(target, 0) + 1
        log.info("  bought %s", target)
        self.dismiss_dialog()  # close any follow-up dialog so the screen is clean

    # ---- refresh ---------------------------------------------------------
    def refresh(self) -> bool:
        """Returns True if a refresh was performed."""
        self.dismiss_dialog()  # clear any stray popup before acting
        screen = self.grab()
        btn = self.find("refresh_button", screen)
        if not btn:
            log.warning("Refresh button not found.")
            self.save_debug(screen, "no_refresh")
            return False
        self.click_match(btn, self.d["after_click"])
        screen = self.grab()
        confirm = self.find("refresh_confirm", screen)
        if not confirm:
            log.warning("Refresh confirm dialog not found (out of skystones?).")
            self.save_debug(screen, "no_refresh_confirm")
            self.dismiss_dialog()
            return False
        self.click_match(confirm, self.d["after_refresh"])
        return True

    def wait_for_shop(self) -> bool:
        deadline = time.time() + self.cfg["max_wait_refresh"]
        while time.time() < deadline:
            if _abort:
                return False
            screen = self.grab()
            if V.on_secret_shop_screen(screen, self.th):
                return True
            time.sleep(self.d["loop_idle"])
        return False

    def close(self):
        self.cap.close()


# --------------------------------------------------------------------------- dry run
def dry_run(bot: "Bot"):
    bot.fit_window()
    screen = bot.grab()
    matches = {}
    for name in ["shop_marker", "refresh_button"] + list(bot.cfg["buy_targets"]):
        try:
            matches[name] = bot.find_all(name, screen) or (
                [m] if (m := bot.find(name, screen)) else []
            )
        except FileNotFoundError as e:
            log.warning("%s", e)
    out = V.annotate(screen, matches)
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dryrun.png")
    cv2.imwrite(path, out)
    for name, ms in matches.items():
        log.info("%-18s %s", name, [f"({m.x},{m.y}) {m.score:.2f}" for m in ms] or "none")
    log.info("Annotated detection saved to %s", path)


# --------------------------------------------------------------------------- main loop
def run(bot: "Bot"):
    budget = bot.cfg["skystone_budget"]
    st = bot.stats
    bot.started_at = time.time()

    bot.fit_window()
    if not V.on_secret_shop_screen(bot.grab(), bot.th):
        log.error("Not on the Secret Shop screen. Open the Secret Shop and retry.")
        return

    log.info("Starting. Skystone budget for refreshes: %d", budget)
    while not _abort:
        cost = REFRESH_COST
        if st["skystones_spent"] + cost > budget:
            log.info("Budget reached (spent %d, next refresh %d, budget %d). Stopping.",
                     st["skystones_spent"], cost, budget)
            break

        bot.dismiss_dialog()  # clear any stray popup (leftover or manual) before acting
        screen = bot.grab()
        if not V.on_secret_shop_screen(screen, bot.th):
            log.warning("Lost the Secret Shop screen — pausing.")
            bot.save_debug(screen, "lost_screen")
            break

        bot.buy_targets()
        if _abort:
            break

        if not bot.refresh():
            log.info("Stopping: could not refresh.")
            break

        st["skystones_spent"] += cost
        st["refreshes"] += 1
        log.info("Refresh #%d done. Skystones spent: %d / %d",
                 st["refreshes"], st["skystones_spent"], budget)

        if not bot.wait_for_shop():
            log.warning("Shop did not reappear in time — pausing.")
            bot.save_debug(bot.grab(), "no_reappear")
            break

    bought = ", ".join(f"{n}:{c}" for n, c in st["bought"].items()) or "none"
    log.info("Finished. %d refreshes, ~%d skystones spent. Bought: %s",
             st["refreshes"], st["skystones_spent"], bought)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="detect & annotate only; no clicks")
    ap.add_argument("--budget", type=int, help="override skystone_budget")
    ap.add_argument("--config", default=CONFIG_PATH)
    args = ap.parse_args()

    cfg = load_config(args.config)
    if args.budget is not None:
        cfg["skystone_budget"] = args.budget

    gw = W.find_game_window()
    if gw is None:
        log.error("Epic Seven window not found. Is the game running?")
        sys.exit(1)
    log.info("Window: hwnd=%s %dx%d mode=%s capture=%s",
             gw.hwnd, gw.width, gw.height, cfg["mode"], cfg["capture_backend"])

    bot = Bot(cfg, gw)
    try:
        if args.dry_run:
            dry_run(bot)
            return
        _install_abort(cfg.get("abort_hotkey", "f12"))
        run(bot)
    except KeyboardInterrupt:
        log.info("Interrupted.")
    finally:
        bot.close()


if __name__ == "__main__":
    main()
