"""E7SSRefresher control panel — a GUI so you never need the command line.

Run by double-clicking "Start E7SSRefresher.bat" (or: py gui.py).

Tabs of buttons map to what the scripts used to do:
  Detect Game     -> find the EpicSeven.exe window
  Test Capture    -> grab one frame with the chosen backend (the probe's capture test)
  Snapshot        -> save a full-client image to crop templates from
  Dry Run         -> detect & annotate without clicking
  Start / Stop    -> run / stop the refresher loop
  Test Click      -> fire one background click at given coords (the probe's input test)
All settings are editable here and saved to config.json.
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

from PIL import Image, ImageTk

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import window as W       # noqa: E402
import vision as V       # noqa: E402
import refresher as R    # noqa: E402

TEMPLATES_DIR = os.path.join(HERE, "templates")
SNAPSHOT_PATH = os.path.join(HERE, "tools", "snapshot.png")
CAPTURE_PATH = os.path.join(HERE, "gui_capture.png")
DRYRUN_PATH = os.path.join(HERE, "dryrun.png")

BUY_OPTIONS = ["covenant_bookmark", "mystic_medal"]


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
        root.geometry("980x640")
        root.minsize(900, 560)

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
        self.detect_game()  # initial status

    # ----------------------------------------------------------------- layout
    def _build_ui(self):
        left = ttk.Frame(self.root, padding=10)
        left.pack(side="left", fill="y")
        right = ttk.Frame(self.root, padding=(0, 10, 10, 10))
        right.pack(side="right", fill="both", expand=True)

        # --- status
        self.status_var = tk.StringVar(value="…")
        ttk.Label(left, text="Status", font=("", 10, "bold")).pack(anchor="w")
        ttk.Label(left, textvariable=self.status_var, foreground="#444",
                  wraplength=240, justify="left").pack(anchor="w", pady=(0, 6))
        b = ttk.Button(left, text="Detect Game", command=self.detect_game)
        b.pack(fill="x")
        self._action_buttons.append(b)

        ttk.Separator(left).pack(fill="x", pady=8)

        # --- settings
        ttk.Label(left, text="Settings", font=("", 10, "bold")).pack(anchor="w")
        grid = ttk.Frame(left)
        grid.pack(fill="x", pady=2)

        self.budget_var = tk.StringVar(value=str(self.cfg.get("skystone_budget", 30)))
        self.thresh_var = tk.StringVar(value=str(self.cfg.get("match_threshold", 0.85)))
        self.mode_var = tk.StringVar(value=self.cfg.get("mode", "background"))
        self.backend_var = tk.StringVar(value=self.cfg.get("capture_backend", "wgc"))

        def row(r, label, widget):
            ttk.Label(grid, text=label).grid(row=r, column=0, sticky="w", pady=2)
            widget.grid(row=r, column=1, sticky="ew", pady=2)

        grid.columnconfigure(1, weight=1)
        row(0, "Skystone budget", ttk.Entry(grid, textvariable=self.budget_var, width=12))
        row(1, "Match threshold", ttk.Entry(grid, textvariable=self.thresh_var, width=12))
        row(2, "Mode", ttk.Combobox(grid, textvariable=self.mode_var, width=12,
                                     state="readonly",
                                     values=["background", "hybrid", "foreground"]))
        row(3, "Capture backend", ttk.Combobox(grid, textvariable=self.backend_var,
                                                width=12, state="readonly",
                                                values=["wgc", "printwindow"]))

        ttk.Label(left, text="Buy targets").pack(anchor="w", pady=(6, 0))
        self.buy_vars: dict[str, tk.BooleanVar] = {}
        enabled = set(self.cfg.get("buy_targets", []))
        for name in BUY_OPTIONS:
            v = tk.BooleanVar(value=name in enabled)
            self.buy_vars[name] = v
            ttk.Checkbutton(left, text=name, variable=v).pack(anchor="w")

        save_btn = ttk.Button(left, text="Save Settings", command=self.save_config)
        save_btn.pack(fill="x", pady=(6, 0))
        self._action_buttons.append(save_btn)

        ttk.Separator(left).pack(fill="x", pady=8)

        # --- setup actions
        ttk.Label(left, text="Setup", font=("", 10, "bold")).pack(anchor="w")
        for text, cmd in [
            ("Test Capture", self.test_capture),
            ("Snapshot for templates", self.snapshot),
            ("Open templates folder", self.open_templates),
            ("Dry Run (detect only)", self.dry_run),
            ("Test Background Click", self.test_click),
        ]:
            b = ttk.Button(left, text=text, command=cmd)
            b.pack(fill="x", pady=1)
            self._action_buttons.append(b)

        ttk.Separator(left).pack(fill="x", pady=8)

        # --- run
        ttk.Label(left, text="Run", font=("", 10, "bold")).pack(anchor="w")
        runrow = ttk.Frame(left)
        runrow.pack(fill="x")
        self.start_btn = ttk.Button(runrow, text="▶ Start", command=self.start)
        self.start_btn.pack(side="left", expand=True, fill="x", padx=(0, 2))
        self._action_buttons.append(self.start_btn)
        self.stop_btn = ttk.Button(runrow, text="■ Stop", command=self.stop, state="disabled")
        self.stop_btn.pack(side="left", expand=True, fill="x", padx=(2, 0))

        # --- right: preview + log
        ttk.Label(right, text="Preview").pack(anchor="w")
        self.preview = ttk.Label(right, text="(captures and dry-run results show here)",
                                 anchor="center", relief="groove")
        self.preview.pack(fill="both", expand=False, pady=(0, 8), ipady=4)
        self.preview.configure(width=70)

        # --- stats
        stats = ttk.LabelFrame(right, text="Stats", padding=8)
        stats.pack(fill="x", pady=(0, 8))
        self._stat_rows = [
            ("refreshes", "Refreshes"),
            ("skystones", "Skystones spent"),
            ("covenant_bookmark", "Covenant bought"),
            ("mystic_medal", "Mystic bought"),
            ("elapsed", "Elapsed"),
        ]
        self.stat_vars = {k: tk.StringVar(value="0") for k, _ in self._stat_rows}
        self.stat_vars["elapsed"].set("0m 00s")
        for i, (key, label) in enumerate(self._stat_rows):
            col = (i % 2) * 2
            row = i // 2
            ttk.Label(stats, text=label + ":").grid(row=row, column=col, sticky="w",
                                                    padx=(0, 6), pady=2)
            ttk.Label(stats, textvariable=self.stat_vars[key],
                      font=("", 10, "bold")).grid(row=row, column=col + 1, sticky="w",
                                                  padx=(0, 24), pady=2)

        ttk.Label(right, text="Log").pack(anchor="w")
        self.txt = scrolledtext.ScrolledText(right, height=14, state="disabled",
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

    def show_image(self, path: str):
        if not os.path.exists(path):
            return
        try:
            img = Image.open(path)
            img.thumbnail((560, 300))
            photo = ImageTk.PhotoImage(img)
            self.preview.configure(image=photo, text="")
            self.preview.image = photo  # keep ref
        except Exception as e:
            R.log.warning("preview failed: %s", e)

    # ----------------------------------------------------------------- config
    def collect_config(self) -> dict:
        cfg = dict(self.cfg)  # keep delays / extras
        try:
            cfg["skystone_budget"] = int(self.budget_var.get())
            cfg["match_threshold"] = float(self.thresh_var.get())
        except ValueError:
            messagebox.showerror("Invalid input",
                                 "Budget must be a whole number, threshold a decimal.")
            raise
        cfg["mode"] = self.mode_var.get()
        cfg["capture_backend"] = self.backend_var.get()
        cfg["buy_targets"] = [n for n, v in self.buy_vars.items() if v.get()]
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
            title = W.window_title(gw.hwnd)
            self.status_var.set(f"Found '{title}'\nhwnd={gw.hwnd}  {gw.width}x{gw.height}")
        return gw

    def _busy(self, fn):
        """Run an action in a worker thread, but only ONE at a time. Concurrent capture
        sessions/clicks race and crash, so we serialize with a lock, disable the buttons
        while running, and log any exception (workers are otherwise silent under pyw)."""
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

    def test_capture(self):
        def work():
            gw = W.find_game_window()
            if gw is None:
                R.log.error("Game not found.")
                return
            backend = self.backend_var.get()
            R.log.info("Capturing with %s ...", backend)
            try:
                cap = W.make_capture(gw.hwnd, backend)
                img = cap.capture()
                cap.close()
            except Exception as e:
                R.log.error("Capture failed: %s", e)
                return
            import cv2
            cv2.imwrite(CAPTURE_PATH, img)
            mean, std = float(img.mean()), float(img.std())
            verdict = "BLACK/blank" if mean < 5 or std < 3 else "looks good"
            R.log.info("Capture %s  mean=%.1f std=%.1f -> %s",
                       img.shape, mean, std, verdict)
            self._ui(lambda: self.show_image(CAPTURE_PATH))
        self._busy(work)

    def snapshot(self):
        def work():
            gw = W.find_game_window()
            if gw is None:
                R.log.error("Game not found.")
                return
            try:
                cap = W.make_capture(gw.hwnd, self.backend_var.get())
                img = cap.capture()
                cap.close()
                import cv2
                os.makedirs(os.path.dirname(SNAPSHOT_PATH), exist_ok=True)
                cv2.imwrite(SNAPSHOT_PATH, img)
            except Exception as e:
                R.log.error("Snapshot failed: %s", e)
                return
            R.log.info("Saved snapshot: %s", SNAPSHOT_PATH)
            R.log.info("Crop items/dialogs from it into templates/ (see templates/README.txt).")
            self._ui(lambda: self.show_image(SNAPSHOT_PATH))
            try:
                os.startfile(SNAPSHOT_PATH)  # noqa: S606 - opens default image viewer
            except Exception:
                pass
        self._busy(work)

    def open_templates(self):
        try:
            os.startfile(TEMPLATES_DIR)
        except Exception as e:
            R.log.error("Could not open folder: %s", e)

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
            self._ui(lambda: self.show_image(DRYRUN_PATH))
        self._busy(work)

    def test_click(self):
        """Decisive background-click check: click the real Refresh button, measure whether
        the screen reacts (the confirm dialog), then auto-cancel. Spends nothing."""
        def work():
            import time, cv2
            gw = W.find_game_window()
            if gw is None:
                R.log.error("Game not found.")
                return
            try:
                th = float(self.thresh_var.get())
            except ValueError:
                th = 0.85
            try:
                cap = W.make_capture(gw.hwnd, self.backend_var.get())
                before = cap.capture()
            except Exception as e:
                R.log.error("Capture failed: %s", e)
                return
            inp = W.PostMessageInput(gw.hwnd)
            btn = V.find("refresh_button", before, min(th, 0.8))
            if btn is None:
                cap.close()
                R.log.error("Refresh button not found — open the Secret Shop and retry.")
                return
            R.log.info("Clicking Refresh at (%d,%d) to test background input ...", btn.x, btn.y)
            inp.click(btn.x, btn.y)
            time.sleep(1.2)
            after = cap.capture()
            changed = float(cv2.absdiff(before, after).mean())
            if changed > 1.5:
                R.log.info("RESULT: background clicks WORK (screen changed %.1f). "
                           "Keep Mode = background.", changed)
                cancel = V.find("cancel_button", after, 0.8)
                if cancel:
                    inp.click(cancel.x, cancel.y)
                    R.log.info("Closed the confirm dialog. Nothing was spent.")
                else:
                    R.log.info("Please click Cancel in-game to close the dialog.")
            else:
                R.log.info("RESULT: no effect (change %.1f) — background clicks are ignored. "
                           "Set Mode = hybrid or foreground.", changed)
            cap.close()
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
                R.log.info("Run started (mode=%s, backend=%s, budget=%d).",
                           cfg["mode"], cfg["capture_backend"], cfg["skystone_budget"])
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
        self._update_stats(force=True)  # capture final numbers
        self._running = False
        self._set_actions_enabled(True)
        self.stop_btn.configure(state="disabled")


def _relaunch_as_admin() -> bool:
    """Relaunch this program elevated (UAC prompt). Returns True if an elevated copy was
    started (caller should exit). Epic Seven runs elevated, so PostMessage clicks are
    blocked (Access denied) unless we match its privilege level."""
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
            return  # elevated instance launched; quit this non-elevated one
        # elevation was declined or failed — run anyway, App will warn about clicks

    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista")
    except Exception:
        pass
    App(root)
    if not is_admin():
        R.log.warning("NOT running as administrator — Epic Seven runs elevated, so clicks "
                      "will fail with 'Access denied'. Close this and relaunch, accepting "
                      "the UAC prompt (or right-click the .bat > Run as administrator).")
    root.mainloop()


if __name__ == "__main__":
    main()
