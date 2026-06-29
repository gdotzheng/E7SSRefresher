"""E7SSRefresher control panel — a small GUI so you never need the command line.

Run by double-clicking "Start E7SSRefresher.bat" (or: py gui.py).

  Detect Game   -> find the EpicSeven.exe window (runs on open)
  Dry Run       -> resize + detect templates without clicking (sanity check)
  Start / Stop  -> run / stop the refresher loop

Buy targets are fixed to Covenant Bookmarks + Mystic Medals. Other tuning lives in config.json.
"""

from __future__ import annotations

import os
import sys
import json
import queue
import ctypes
import logging
import threading
import time

import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import window as W       # noqa: E402
import refresher as R    # noqa: E402

DRYRUN_PATH = os.path.join(HERE, "dryrun.png")
BUY_TARGETS = ["covenant_bookmark", "mystic_medal"]


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


class QueueLogHandler(logging.Handler):
    def __init__(self, q: "queue.Queue[str]"):
        super().__init__()
        self.q = q

    def emit(self, record):
        self.q.put(self.format(record))


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("E7SSRefresher — Secret Shop")
        root.geometry("640x560")
        root.minsize(560, 480)

        self.cfg = R.load_config()
        self.log_q: "queue.Queue[str]" = queue.Queue()
        self._running = False
        self._run_thread: threading.Thread | None = None
        self._action_lock = threading.Lock()
        self._action_buttons: list[ttk.Button] = []
        self._bot = None  # set while a run is active, for live stats

        self._build_ui()
        self._wire_logging()
        self.root.after(120, self._poll_log)
        self.detect_game()

    # ----------------------------------------------------------------- layout
    def _build_ui(self):
        main = ttk.Frame(self.root, padding=10)
        main.pack(fill="both", expand=True)

        # --- status
        top = ttk.Frame(main)
        top.pack(fill="x")
        self.status_var = tk.StringVar(value="…")
        ttk.Label(top, text="Status", font=("", 10, "bold")).pack(anchor="w")
        ttk.Label(top, textvariable=self.status_var, foreground="#444",
                  wraplength=420, justify="left").pack(side="left", anchor="w")
        b = ttk.Button(top, text="Detect Game", command=self.detect_game)
        b.pack(side="right")
        self._action_buttons.append(b)

        ttk.Separator(main).pack(fill="x", pady=8)

        # --- controls
        ctrl = ttk.Frame(main)
        ctrl.pack(fill="x")
        ttk.Label(ctrl, text="Skystone budget").pack(side="left")
        self.budget_var = tk.StringVar(value=str(self.cfg.get("skystone_budget", 30)))
        ttk.Entry(ctrl, textvariable=self.budget_var, width=10).pack(side="left", padx=(6, 6))
        save_btn = ttk.Button(ctrl, text="Save", command=self.save_config)
        save_btn.pack(side="left")
        self._action_buttons.append(save_btn)
        dry_btn = ttk.Button(ctrl, text="Dry Run", command=self.dry_run)
        dry_btn.pack(side="right")
        self._action_buttons.append(dry_btn)

        runrow = ttk.Frame(main)
        runrow.pack(fill="x", pady=(8, 0))
        self.start_btn = ttk.Button(runrow, text="▶ Start", command=self.start)
        self.start_btn.pack(side="left", expand=True, fill="x", padx=(0, 2))
        self._action_buttons.append(self.start_btn)
        self.stop_btn = ttk.Button(runrow, text="■ Stop", command=self.stop, state="disabled")
        self.stop_btn.pack(side="left", expand=True, fill="x", padx=(2, 0))

        ttk.Separator(main).pack(fill="x", pady=8)

        # --- stats
        stats = ttk.LabelFrame(main, text="Stats", padding=8)
        stats.pack(fill="x")
        rows = [
            ("refreshes", "Refreshes"),
            ("skystones", "Skystones spent"),
            ("covenant_bookmark", "Covenant bought"),
            ("mystic_medal", "Mystic bought"),
            ("elapsed", "Elapsed"),
        ]
        self.stat_vars = {k: tk.StringVar(value="0") for k, _ in rows}
        self.stat_vars["elapsed"].set("0m 00s")
        for i, (key, label) in enumerate(rows):
            col = (i % 2) * 2
            ttk.Label(stats, text=label + ":").grid(row=i // 2, column=col, sticky="w",
                                                    padx=(0, 6), pady=2)
            ttk.Label(stats, textvariable=self.stat_vars[key],
                      font=("", 10, "bold")).grid(row=i // 2, column=col + 1, sticky="w",
                                                  padx=(0, 24), pady=2)

        # --- log
        ttk.Label(main, text="Log").pack(anchor="w", pady=(8, 0))
        self.txt = scrolledtext.ScrolledText(main, height=12, state="disabled",
                                             font=("Consolas", 9), wrap="word")
        self.txt.pack(fill="both", expand=True)

    # ----------------------------------------------------------------- logging
    def _wire_logging(self):
        h = QueueLogHandler(self.log_q)
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S"))
        R.log.addHandler(h)
        R.log.setLevel(logging.INFO)

    def _poll_log(self):
        try:
            while True:
                msg = self.log_q.get_nowait()
                self.txt.configure(state="normal")
                self.txt.insert("end", msg + "\n")
                self.txt.see("end")
                self.txt.configure(state="disabled")
        except queue.Empty:
            pass
        self._update_stats()
        self.root.after(120, self._poll_log)

    def _update_stats(self, force: bool = False):
        b = self._bot
        if b is None or (not self._running and not force):
            return
        st = b.stats
        self.stat_vars["refreshes"].set(str(st["refreshes"]))
        self.stat_vars["skystones"].set(str(st["skystones_spent"]))
        self.stat_vars["covenant_bookmark"].set(str(st["bought"].get("covenant_bookmark", 0)))
        self.stat_vars["mystic_medal"].set(str(st["bought"].get("mystic_medal", 0)))
        if b.started_at:
            el = int(time.time() - b.started_at)
            self.stat_vars["elapsed"].set(f"{el // 60}m {el % 60:02d}s")

    def _reset_stats(self):
        for k in self.stat_vars:
            self.stat_vars[k].set("0")
        self.stat_vars["elapsed"].set("0m 00s")

    def _ui(self, fn):
        self.root.after(0, fn)

    # ----------------------------------------------------------------- helpers
    def _busy(self, fn):
        """Run an action in a worker thread, one at a time, logging any exception."""
        def runner():
            if not self._action_lock.acquire(blocking=False):
                R.log.info("Busy — wait for the current action to finish.")
                return
            self._ui(lambda: self._set_actions_enabled(False))
            try:
                fn()
            except Exception:
                import traceback
                R.log.error("Action failed:\n%s", traceback.format_exc())
            finally:
                self._action_lock.release()
                self._ui(lambda: self._set_actions_enabled(True))
        threading.Thread(target=runner, daemon=True).start()

    def _set_actions_enabled(self, enabled: bool):
        state = "normal" if enabled else "disabled"
        for b in self._action_buttons:
            try:
                b.configure(state=state)
            except Exception:
                pass

    # ----------------------------------------------------------------- config
    def collect_config(self) -> dict:
        cfg = dict(self.cfg)  # keep mode/backend/threshold/etc. from config.json
        try:
            cfg["skystone_budget"] = int(self.budget_var.get())
        except ValueError:
            messagebox.showerror("Invalid input", "Skystone budget must be a whole number.")
            raise
        cfg["buy_targets"] = list(BUY_TARGETS)
        return cfg

    def save_config(self):
        try:
            cfg = self.collect_config()
        except ValueError:
            return
        self.cfg = cfg
        with open(R.CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
        R.log.info("Settings saved to config.json")

    # ----------------------------------------------------------------- actions
    def detect_game(self):
        gw = W.find_game_window()
        if gw is None:
            self.status_var.set("Epic Seven NOT found.\nLaunch the game, then Detect again.")
        else:
            self.status_var.set(f"Found '{W.window_title(gw.hwnd)}'  ({gw.width}x{gw.height})")
        return gw

    def dry_run(self):
        def work():
            try:
                cfg = self.collect_config()
            except ValueError:
                return
            gw = W.find_game_window()
            if gw is None:
                R.log.error("Game not found.")
                return
            try:
                bot = R.Bot(cfg, gw)
            except Exception as e:
                R.log.error("Could not start capture: %s", e)
                return
            try:
                R.dry_run(bot)
            finally:
                bot.close()
            try:
                os.startfile(DRYRUN_PATH)  # open the annotated result in the default viewer
            except Exception:
                pass
        self._busy(work)

    # ----------------------------------------------------------------- run loop
    def start(self):
        if self._running:
            return
        try:
            cfg = self.collect_config()
        except ValueError:
            return
        gw = W.find_game_window()
        if gw is None:
            R.log.error("Game not found. Launch Epic Seven and open the Secret Shop.")
            return

        self._running = True
        self._set_actions_enabled(False)
        self.stop_btn.configure(state="normal")
        self._reset_stats()
        R.reset_abort()

        def work():
            bot = None
            try:
                bot = R.Bot(cfg, gw)
                self._bot = bot
                R.log.info("Run started (budget=%d).", cfg["skystone_budget"])
                R.run(bot)
            except Exception as e:
                R.log.error("Run error: %s", e)
            finally:
                if bot is not None:
                    bot.close()
                self._ui(self._on_run_finished)

        self._run_thread = threading.Thread(target=work, daemon=True)
        self._run_thread.start()

    def stop(self):
        if self._running:
            R.request_abort()
            self.stop_btn.configure(state="disabled")

    def _on_run_finished(self):
        self._update_stats(force=True)
        self._running = False
        self._set_actions_enabled(True)
        self.stop_btn.configure(state="disabled")


def _relaunch_as_admin() -> bool:
    """Relaunch elevated (UAC). Epic Seven runs elevated, so PostMessage clicks are blocked
    (Access denied) unless we match its privilege level."""
    script = os.path.abspath(sys.argv[0])
    try:
        rc = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable, f'"{script}"', HERE, 1)
        return rc is not None and rc > 32
    except Exception:
        return False


def main():
    if os.name == "nt" and not is_admin():
        if _relaunch_as_admin():
            return  # elevated instance launched; quit this one
        # elevation declined/failed — run anyway, App will warn

    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista")
    except Exception:
        pass
    App(root)
    if not is_admin():
        R.log.warning("NOT running as administrator — Epic Seven runs elevated, so clicks "
                      "will fail with 'Access denied'. Relaunch and accept the UAC prompt.")
    root.mainloop()


if __name__ == "__main__":
    main()
