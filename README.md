# E7SSRefresher — Epic Seven Secret Shop auto-refresher

Grinds the Epic Seven **Secret Shop** for you: each cycle it buys **Covenant Bookmarks** and
**Mystic Medals** if they're in stock, refreshes the shop (3 skystones), and repeats until your
**skystone budget** is spent.

It runs **in the background** — Epic Seven can sit behind other windows while you keep using your
PC. Your mouse and keyboard are never taken over. It works purely by **screen capture → template
matching → clicking** (Windows Graphics Capture + OpenCV + `PostMessage`); there is no game memory
reading or packet manipulation.

> ⚠️ Automating gameplay may violate Epic Seven's Terms of Service and could put your
> account at risk. Use at your own discretion.

## Get it running (2 minutes, no Python)

1. Download **`E7SSRefresher.exe`** from the
   [Releases page](https://github.com/gdotzheng/E7SSRefresher/releases/latest)
   (or `gh release download <tag> -R gdotzheng/E7SSRefresher`). It's one self-contained file and
   can live anywhere.
2. Launch **Epic Seven** in windowed mode and open the **Secret Shop**.
3. Run the .exe and **accept the UAC prompt**. Epic Seven runs elevated, so the app must too —
   otherwise every click fails with "Access is denied".
4. Set your **skystone budget** (saved automatically when you start), then **▶ Start**. The game
   window is resized once to the resolution the templates were captured at, then the loop takes
   over. You can alt-tab away.

Settings and output live in **`%APPDATA%\E7SSRefresher\`** — an editable `config.json` (seeded on
first run) plus any `debug_*.png` the bot saves when it hits an unexpected screen.

## The control panel

A frameless pywebview window (`webui/index.html` rendered in Edge WebView2, built into Win11):

- **Status** – auto-detects Epic Seven every 2s and shows its client size. No button to press; the
  dot goes green when the game is found.
- **Skystone budget** – how many skystones to burn on refreshes (each refresh = 3). Saved
  automatically when you press Start.
- **Buy Friendship Bookmarks** – also buy Friendship Bookmarks when they show up (Covenant
  Bookmarks and Mystic Medals are always bought).
- **Auto-resume when returning to shop** – checked: navigating away from the Secret Shop *pauses*
  the run and it picks back up when you return. Unchecked: leaving the shop ends the run.
- **▶ Start / ■ Stop**.
- **Stats** – refreshes, skystones spent, budget left, Covenant/Mystic (and Friendship, when enabled)
  bought (each with an average *skystones per item*), elapsed time.
- **Activity log** – colour-coded (buys green, refreshes blue, warnings amber).
- **Dark mode** toggle – remembered across launches.

The bot runs in a background thread and Python *pushes* updates into the page, so the loop keeps
going and the stats keep ticking even while the panel is unfocused and you're looking at the game.

## What each cycle does

1. Dismiss any leftover popup, and verify the Secret Shop is actually on screen (`shop_marker`).
2. Scroll the shop list top → bottom, and on each page buy every target item whose **Buy button is
   green**. A greyed-out `0/1` button means already bought — it's skipped silently.
3. Click **Refresh** and confirm.
4. Wait for the shop to redraw, then repeat until `spent + 3 > budget`.

It's deliberately cautious about clicking the wrong thing:

- It never confirms a purchase unless a real purchase popup actually opened (a stray shop "Buy"
  button can otherwise match the confirm template and buy something expensive).
- Stray dialogs — including ones you opened by hand mid-run — get cancelled, not clicked through.
- **Minimized game** → capture is impossible, so it pauses and resumes when you restore it
  (behind other windows is fine, minimized is not).
- A missed refresh dialog is treated as transient: it polls, retries, and only stops after
  `max_consecutive_fails` bad cycles in a row.
- On an unrecognized screen it saves a `debug_*.png` and pauses instead of clicking blindly.

## Running from source

```
py -m pip install -r requirements.txt
```

Then double-click **`Start E7SSRefresher.bat`** for the panel (or `py gui.py` to see errors in a
console). Run the terminal **as administrator** — same elevation requirement as the .exe.

There's also a CLI, which is the only way to use options the panel doesn't expose:

```
py refresher.py                 # uses config.json
py refresher.py --budget 12     # override the skystone budget for this run
py refresher.py --dry-run       # detect & annotate only, no clicks -> dryrun.png
```

Press **F12** (configurable) or **Ctrl+C** to stop a CLI run.

## Building and publishing

- `build.bat` → **`dist\E7SSRefresher.exe`** (PyInstaller, one file, templates baked in,
  `--uac-admin` so it prompts for elevation automatically).
- `release.bat v2.0.8 "what changed"` → builds it and creates a GitHub Release with the exe
  attached (needs the `gh` CLI, authenticated).

## Configuration (`config.json`)

Everything below is optional tuning — the shipped defaults are the verified working setup.

| key | meaning |
|---|---|
| `skystone_budget` | stop once this many skystones have gone into refreshes (each refresh = 3) |
| `keep_alive_on_leave` | pause & auto-resume when you leave the Secret Shop (`true`), or end the run (`false`) — the panel's checkbox |
| `dark_mode` | panel theme; also toggled from the panel |
| `auto_resize` + `target_window` | on Start, resize the game's *client* area to `[w,h]` (the size the templates were captured at) so matching works no matter how you left the window |
| `match_threshold` | template-match confidence (0–1); raise if it misclicks, lower if it misses |
| `buy_green_dom` | min green-dominance for a Buy button to count as available; this is what distinguishes buyable from already-bought (greyed `0/1`) |
| `scales` | extra match scales for minor resolution differences (e.g. `[1.0, 0.95, 1.05]`) |
| `delays` | pacing between clicks / buys / refreshes |
| `max_wait_refresh` | seconds to wait for the shop to reappear after a refresh |
| `max_consecutive_fails` | failed cycles in a row before giving up; transient misses are retried, not fatal |
| `scroll` | how the item list is walked: `point` (where to wheel), `step_notches`, `max_pages`, `list_region_x`/`change_threshold` (bottom detection, measured on the list only so the animated character art doesn't count as movement) |
| `abort_hotkey` | CLI stop key (default `f12`) |
| `buy_targets` | **CLI only** — the panel always buys Covenant Bookmarks + Mystic Medals, plus `friendship_points` when the checkbox is on |
| `buy_friendship` | the panel's **Buy Friendship Bookmarks** checkbox |
| `mode` / `capture_backend` | input and capture backends, `background`/`wgc` by default and verified working. Only change these if capture comes back black — see Troubleshooting |

## Files

```
gui.py            control panel — pywebview Python<->JS bridge; launched by "Start E7SSRefresher.bat"
webui/index.html  the panel's HTML/CSS/JS (rendered in Edge WebView2)
refresher.py      main loop / state machine (+ CLI --dry-run)
window.py         window lookup, capture (WGC / PrintWindow), input (PostMessage / real cursor)
vision.py         OpenCV template matching helpers
config.json       default settings (the exe seeds a copy into %APPDATA%\E7SSRefresher\)
templates/        cropped button/item PNGs (bundled into the exe)
tools/snip.py     grab a frame to crop new templates from;  tools/probe.py  capture/click diagnostic
build.bat         build the standalone .exe (PyInstaller)
release.bat       build + publish a GitHub Release (gh CLI)
```

## Troubleshooting

- **"Access is denied" / clicks do nothing** — the game runs elevated, so the app must too.
  Relaunch and accept the UAC prompt (from source: right-click the .bat → Run as administrator).
- **Epic Seven minimized** — a minimized window can't be captured, so the bot pauses and says so.
  Restore it (behind other windows is fine) and it resumes.
- **"Epic Seven not found"** — the game must be running; the app looks for `EpicSeven.exe`.
- **Captures are black** — rare, but if your setup can't do Windows Graphics Capture, set
  `capture_backend` to `printwindow`. If clicks are also ignored, set `mode` to `foreground` and
  keep E7 as the front window. `py tools/probe.py` writes `probe_wgc.png` / `probe_printwindow.png`
  so you can see which backend actually reads your client, and `--click X Y` tests background clicks.
- **Misses or misclicks** — the bundled templates are cropped at 1108×623 and `auto_resize` puts the
  game there, so this is usually a threshold issue first (`match_threshold`). If your client renders
  differently, recapture with `py tools/snip.py --backend wgc` and re-crop the PNGs listed in
  `templates/README.txt`. `py refresher.py --dry-run` writes `dryrun.png` with boxes on everything
  it detected, which is the fastest way to see what's wrong.
- **Abort hotkey didn't arm (CLI)** — the `keyboard` library needs an elevated terminal;
  Ctrl+C still works.
