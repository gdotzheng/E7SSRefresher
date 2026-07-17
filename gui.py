"""E7SSRefresher control panel — a pywebview (HTML/CSS) front-end over the bot backend.

The window renders webui/index.html in Edge WebView2; this module is just the Python<->JS
bridge. The bot logic (window.py, vision.py, refresher.py) is unchanged.

Run by double-clicking "Start E7SSRefresher.bat" (or: py gui.py).
"""

from __future__ import annotations

import os
import sys
import json
import time
import queue
import ctypes
import logging
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# Keep the Edge WebView2 renderer fully active when the window is in the background. Without
# this, Chromium throttles/suspends JS timers (and even the initial script) whenever the app
# isn't the foreground window — which froze the UI on "Detecting…" while Epic Seven had focus.
# Must be set before WebView2 starts.
os.environ.setdefault(
    "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS",
    "--disable-background-timer-throttling --disable-renderer-backgrounding "
    "--disable-backgrounding-occluded-windows")

import webview          # noqa: E402
import window as W      # noqa: E402
import refresher as R   # noqa: E402


def _res_dir() -> str:
    """Read-only bundled resources (the PyInstaller bundle when frozen, else this dir)."""
    return getattr(sys, "_MEIPASS", HERE)


WEBUI = os.path.join(_res_dir(), "webui", "index.html")

_api = None     # set in main(); referenced by the push loop
_window = None
_shutdown = threading.Event()


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


class QueueLogHandler(logging.Handler):
    """Turn log records into structured {t, lvl, m} entries the UI can colour."""

    def __init__(self, q: "queue.Queue[dict]"):
        super().__init__()
        self.q = q

    def emit(self, record):
        try:
            msg = record.getMessage()
            low = msg.lower()
            if record.levelno >= logging.WARNING:
                lvl = "warn"
            elif "bought" in low or "would buy" in low:
                lvl = "buy"
            elif "refresh #" in low or low.startswith("refresh"):
                lvl = "refresh"
            else:
                lvl = "info"
            self.q.put({"t": time.strftime("%H:%M:%S"), "lvl": lvl, "m": msg})
        except Exception:
            pass


class Api:
    """Methods here are callable from JS as window.pywebview.api.<name>(...)."""

    def __init__(self):
        self.cfg = R.load_config()
        self.log_q: "queue.Queue[dict]" = queue.Queue()
        self._running = False
        self._bot = None
        self._elapsed = "0m 00s"
        self._det = {"detected": False, "status": "Detecting…", "size": ""}
        R.log.addHandler(QueueLogHandler(self.log_q))
        R.log.setLevel(logging.INFO)
        # Game detection scans every process (slow) — run it off the bridge thread so poll()
        # stays instant and never freezes the UI.
        threading.Thread(target=self._detect_loop, daemon=True).start()

    # ---------------------------------------------------------------- exposed
    def get_init(self):
        return {"budget": int(self.cfg.get("skystone_budget", 3000)),
                "dark": bool(self.cfg.get("dark_mode", True))}

    def _detect(self):
        try:
            gw = W.find_game_window()
        except Exception as e:
            R.log.warning("detect error: %s", e)
            gw = None
        if gw:
            self._det = {"detected": True, "status": "Epic Seven detected",
                         "size": f"{gw.width} × {gw.height}"}
        else:
            self._det = {"detected": False, "status": "Epic Seven not found", "size": ""}
        return self._det

    def _detect_loop(self):
        while True:
            try:
                self._detect()
            except Exception:
                pass
            time.sleep(2.0)

    def detect_game(self):
        # Return the cached result (kept fresh by the background loop) — never blocks the UI.
        return self._det

    def save(self, budget):
        b = self._parse(budget)
        if b is None:
            R.log.warning("Invalid budget.")
            return {"ok": False}
        self.cfg["skystone_budget"] = b
        self._write_cfg()
        R.log.info("Settings saved (budget %d).", b)
        return {"ok": True}

    def set_dark(self, dark):
        self.cfg["dark_mode"] = bool(dark)
        self._write_cfg()
        return {"ok": True}

    def start(self, budget):
        if self._running:
            return {"ok": False}
        b = self._parse(budget)
        if b is None:
            R.log.error("Invalid budget.")
            return {"ok": False}
        gw = W.find_game_window()
        if gw is None:
            R.log.error("Game not found. Launch Epic Seven and open the Secret Shop.")
            return {"ok": False}
        cfg = dict(self.cfg)
        cfg["skystone_budget"] = b
        cfg["buy_targets"] = ["covenant_bookmark", "mystic_medal"]
        self._running = True
        R.reset_abort()

        def work():
            bot = None
            try:
                bot = R.Bot(cfg, gw)
                self._bot = bot
                R.log.info("Run started (budget=%d).", b)
                R.run(bot)
            except Exception as e:
                R.log.error("Run error: %s", e)
            finally:
                if bot is not None:
                    bot.close()
                self._running = False

        threading.Thread(target=work, daemon=True).start()
        return {"ok": True}

    def stop(self):
        if self._running:
            R.request_abort()
        return {"ok": True}

    def poll(self):
        # Fast: just read cached detection + drain the log queue + read bot stats. No scanning.
        logs = []
        try:
            while True:
                logs.append(self.log_q.get_nowait())
        except queue.Empty:
            pass
        budget = int(self.cfg.get("skystone_budget", 3000))
        b = self._bot
        if b is not None:
            s = b.stats
            spent = s["skystones_spent"]
            if self._running and b.started_at:
                el = int(time.time() - b.started_at)
                self._elapsed = f"{el // 60}m {el % 60:02d}s"
            stats = {"refreshes": s["refreshes"], "spent": spent,
                     "budget_left": max(0, budget - spent),
                     "covenant": s["bought"].get("covenant_bookmark", 0),
                     "mystic": s["bought"].get("mystic_medal", 0),
                     "elapsed": self._elapsed}
        else:
            stats = {"refreshes": 0, "spent": 0, "budget_left": budget,
                     "covenant": 0, "mystic": 0, "elapsed": "0m 00s"}
        d = self._det
        return {"detected": d["detected"], "status": d["status"], "size": d["size"],
                "running": self._running, "stats": stats, "log": logs}

    def minimize(self):
        if _window:
            _window.minimize()

    def close(self):
        if _window:
            _window.destroy()

    # ---------------------------------------------------------------- helpers
    def _parse(self, v):
        digits = "".join(ch for ch in str(v) if ch.isdigit())
        return int(digits) if digits else None

    def _write_cfg(self):
        try:
            with open(R.CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(self.cfg, f, indent=2)
        except Exception as e:
            R.log.warning("Could not save config: %s", e)


def _relaunch_as_admin() -> bool:
    """Relaunch elevated (UAC). Epic Seven runs elevated, so PostMessage clicks are blocked
    unless we match its privilege level."""
    script = os.path.abspath(sys.argv[0])
    try:
        rc = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable, f'"{script}"', HERE, 1)
        return rc is not None and rc > 32
    except Exception:
        return False


def _push_loop():
    """Drive the UI from Python via evaluate_js. Runs in its own thread (started by
    webview.start), so updates land even when the window is in the background and Chromium
    would have throttled a JS-side timer. evaluate_js executes regardless of focus."""
    if _shutdown.wait(0.4):
        return
    try:
        _window.evaluate_js("window.initSettings && window.initSettings(%s); 1"
                            % json.dumps(_api.get_init()))
    except Exception:
        pass
    while not _shutdown.is_set():
        try:
            _window.evaluate_js("window.applyState && window.applyState(%s); 1"
                                % json.dumps(_api.poll()))
        except Exception:
            pass
        _shutdown.wait(0.4)


def main():
    if os.name == "nt" and not is_admin():
        if _relaunch_as_admin():
            return  # elevated instance launched; quit this one

    global _api, _window
    _api = Api()
    with open(WEBUI, "r", encoding="utf-8") as f:
        html = f.read()

    _window = webview.create_window(
        "E7SSRefresher - Background Secret Shop Refresher",
        html=html, js_api=_api,
        width=750, height=810,
        frameless=True, easy_drag=False, resizable=False,
        background_color="#15181e")

    def _on_closed():
        # Stop the bot and the push loop so no thread outlives the window.
        _shutdown.set()
        R.request_abort()

    _window.events.closed += _on_closed

    if not is_admin():
        R.log.warning("NOT running as administrator — Epic Seven runs elevated, so clicks "
                      "will fail with 'Access denied'. Relaunch and accept the UAC prompt.")
    webview.start(_push_loop)
    # webview.start returns once the window is gone. WebView2/pythonnet leave non-daemon CLR
    # threads behind that keep the process alive, so force a hard exit.
    _on_closed()
    os._exit(0)


if __name__ == "__main__":
    main()
