# E7SSRefresher — Epic Seven Secret Shop auto-refresher

Automates the Epic Seven **Secret Shop**: on each cycle it buys your chosen items
(Covenant Bookmarks, Mystic Medals) if they appear, then refreshes, repeating until a
**skystone budget** for refreshes is spent.

It works by **screen capture → template matching → clicking**. There is no game memory
reading or packet manipulation.

> ⚠️ Automating gameplay may violate Epic Seven's Terms of Service and could put your
> account at risk. Use at your own discretion.

## Easiest way: the control panel (no commands)

1. One-time install of dependencies (double-click won't do this): open a terminal in this folder and run
   `py -m pip install -r requirements.txt`.
2. **Double-click `Start E7SSRefresher.bat`** to open the control panel.
   Epic Seven runs as administrator, so the panel will request elevation — **accept the UAC prompt**.
   (Without it, clicks to the game fail with "Access denied".)

From the panel you can do everything without the command line:
- **Detect Game** – confirms Epic Seven is found (runs automatically on open).
- **Test Capture** – grabs one frame with the chosen backend and shows it in the preview.
- **Snapshot for templates** + **Open templates folder** – save and crop your template images.
- **Dry Run** – detect & annotate without clicking.
- **Test Click** – fire one background click at the given x/y to check if background input works.
- Edit **settings** (budget, mode, backend, buy targets) and **Save Settings**.
- **▶ Start / ■ Stop** the refresher, with a live log, image preview, and a **Stats** panel
  (refreshes done, skystones spent, items bought per type, elapsed time).

The sections below describe the same steps via the command line, if you prefer.

## How it runs (background vs foreground)

E7 is an OpenGL game. Reading its window in the background is reliable (Windows Graphics
Capture). Sending **clicks** without focus (`PostMessage`) often does *not* work for games.
So the run **mode** is decided by a one-time probe:

| probe result | `mode` | behavior |
|---|---|---|
| capture OK **and** background click registers | `background` | fully hands-off; use the PC normally |
| capture OK but click ignored | `hybrid` | real cursor moves only at click moments |
| capture black/failed | `foreground` | E7 must stay the front window |

## Setup

1. Install Python 3.11+ (64-bit), then:
   ```
   py -m pip install -r requirements.txt
   ```

2. **Probe (decides the mode).** Launch E7, then place its window *behind* your terminal and run:
   ```
   py tools/probe.py
   ```
   Inspect `tools/probe_wgc.png` / `tools/probe_printwindow.png` — they should show the game,
   not black. To test background clicking, open the Secret Shop and run e.g.
   `py tools/probe.py --click 400 300` while watching whether the button reacts.
   Set `mode` and `capture_backend` in `config.json` per the table above.

3. **Capture templates.** Go to the Secret Shop in-game, then:
   ```
   py tools/snip.py --backend wgc
   ```
   Crop the images listed in `templates/README.txt` out of `tools/snapshot.png` into `templates/`.

4. **Dry run (no clicks).** Confirm everything is detected:
   ```
   py refresher.py --dry-run
   ```
   Check `dryrun.png` — boxes should sit on the refresh button, shop marker, and items.

## Run

Open the Secret Shop in-game, then:
```
py refresher.py                 # uses config.json
py refresher.py --budget 12     # override skystone budget for this run
```
Press **F12** (configurable) or **Ctrl+C** to stop.

## Configuration (`config.json`)

| key | meaning |
|---|---|
| `auto_resize` + `target_window` | on Start/Dry-Run, resize the game's client area to `[w,h]` (the size the templates were captured at) so matching is reliable regardless of how the window was left |
| `mode` | `background` / `hybrid` / `foreground` (from the probe) |
| `capture_backend` | `wgc` or `printwindow` |
| `skystone_budget` | stop once this many skystones have been spent on refreshes (each refresh = 3) |
| `buy_targets` | which item templates to buy each cycle |
| `match_threshold` | template-match confidence (0–1); raise if it misclicks, lower if it misses |
| `buy_green_dom` | min green-dominance for a Buy button to count as available; skips already-bought (greyed `0/1`) items instead of re-attempting |
| `scales` | extra match scales for minor resolution differences (e.g. `[1.0, 0.95, 1.05]`) |
| `delays` | pacing between clicks/buys/refreshes |
| `max_wait_refresh` | seconds to wait for the shop to reappear after a refresh |
| `scroll` | the shop list is scrolled top→bottom each buy pass (mouse-wheel) to see every item; `point` (where to wheel), `step_notches`, `max_pages`, `list_region_x`/`change_threshold` (bottom detection) |

## Files

```
gui.py           control-panel GUI (Tkinter); launched by "Start E7SSRefresher.bat"
refresher.py     main loop / state machine + --dry-run
window.py        window lookup, capture (WGC / PrintWindow), input (PostMessage / cursor)
vision.py        OpenCV template matching helpers
config.json      settings
templates/       your cropped button/item PNGs
tools/probe.py   feasibility probe (run first)
tools/snip.py    capture the client to crop templates from
```

## Troubleshooting

- **"Access is denied" on clicks / clicks do nothing** — the game runs elevated; the panel must run
  as administrator. Relaunch and accept the UAC prompt (or right-click the .bat → Run as administrator).
- **Window not found** — the game must be running; the script targets `EpicSeven.exe`.
- **Captures are black** — use `mode=foreground` / `capture_backend=printwindow`, keep E7 in front.
- **Misses / misclicks** — recapture templates at your exact resolution; tune `match_threshold`.
- **Abort hotkey didn't arm** — the `keyboard` library may need an elevated terminal; Ctrl+C still works.
- On unexpected screens the bot saves a `debug_*.png` and pauses instead of clicking blindly.
