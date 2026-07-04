"""Platform layer: locate the Epic Seven window, capture it, and click it.

Two capture backends and two input backends are provided behind small interfaces so the
rest of the bot does not care whether we are running in background or foreground mode:

  Capture:  WGCCapture (Windows Graphics Capture, works on background/occluded windows)
            PrintWindowCapture (GDI PrintWindow with PW_RENDERFULLCONTENT, simpler fallback)

  Input:    PostMessageInput (no focus needed; may be ignored by the game)
            ForegroundInput  (real cursor via SendInput; needs the window in front)

All coordinates exchanged with the rest of the program are *client-relative* pixels
(0,0 = top-left of the game's drawable area).
"""

from __future__ import annotations

import time
import random
import ctypes
from ctypes import windll
from dataclasses import dataclass

import numpy as np
import psutil
import win32api
import win32con
import win32gui
import win32process
import win32ui

EXE_NAME = "EpicSeven.exe"
PW_RENDERFULLCONTENT = 0x00000002


# --------------------------------------------------------------------------- window lookup
@dataclass
class GameWindow:
    hwnd: int
    width: int
    height: int

    def client_rect(self) -> tuple[int, int, int, int]:
        l, t, r, b = win32gui.GetClientRect(self.hwnd)
        return l, t, r, b


def _pids_for(exe_name: str) -> set[int]:
    pids: set[int] = set()
    for p in psutil.process_iter(["name", "pid"]):
        name = p.info.get("name")
        if name and name.lower() == exe_name.lower():
            pids.add(p.info["pid"])
    return pids


def find_game_window(exe_name: str = EXE_NAME) -> GameWindow | None:
    """Return the largest visible top-level window owned by EpicSeven.exe, or None."""
    pids = _pids_for(exe_name)
    if not pids:
        return None

    candidates: list[GameWindow] = []

    def _cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return True
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        if pid not in pids:
            return True
        l, t, r, b = win32gui.GetClientRect(hwnd)
        w, h = r - l, b - t
        if (w <= 0 or h <= 0) and win32gui.IsIconic(hwnd):
            # Minimized window reports a 0-size client rect — fall back to its restored
            # size so we still detect Epic Seven (it's resized properly on Start).
            try:
                nl, nt, nr, nb = win32gui.GetWindowPlacement(hwnd)[4]
                w, h = max(0, nr - nl), max(0, nb - nt)
            except Exception:
                pass
        if w > 0 and h > 0:
            candidates.append(GameWindow(hwnd=hwnd, width=w, height=h))
        return True

    win32gui.EnumWindows(_cb, None)
    if not candidates:
        return None
    return max(candidates, key=lambda c: c.width * c.height)


def window_title(hwnd: int) -> str:
    return win32gui.GetWindowText(hwnd)


def is_minimized(hwnd: int) -> bool:
    """True if the window is minimized (iconic). WGC/PrintWindow can't capture a minimized
    window, so the bot must wait for it to be restored."""
    try:
        return bool(win32gui.IsIconic(hwnd))
    except Exception:
        return False


def set_client_size(hwnd: int, client_w: int, client_h: int) -> tuple[int, int]:
    """Resize the window so its CLIENT area (the game content) becomes client_w x client_h.
    MoveWindow sizes the outer window, so we add the current non-client border delta.
    Restores the window first (a minimized/maximized window can't be sized). Keeps position.
    Returns the resulting client (w, h)."""
    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    time.sleep(0.1)
    wl, wt, wr, wb = win32gui.GetWindowRect(hwnd)
    _, _, cur_cw, cur_ch = win32gui.GetClientRect(hwnd)
    border_w = (wr - wl) - cur_cw
    border_h = (wb - wt) - cur_ch
    win32gui.MoveWindow(hwnd, wl, wt, client_w + border_w, client_h + border_h, True)
    time.sleep(0.2)
    _, _, ncw, nch = win32gui.GetClientRect(hwnd)
    return ncw, nch


def _visible_window_origin(hwnd: int) -> tuple[int, int]:
    """Top-left of the window's true *visible* bounds in screen coords, via DWM
    (excludes the invisible resize border that GetWindowRect includes). Falls back to
    GetWindowRect if the DWM call fails."""
    from ctypes import wintypes
    DWMWA_EXTENDED_FRAME_BOUNDS = 9
    rect = wintypes.RECT()
    try:
        res = ctypes.windll.dwmapi.DwmGetWindowAttribute(
            wintypes.HWND(hwnd),
            ctypes.c_uint(DWMWA_EXTENDED_FRAME_BOUNDS),
            ctypes.byref(rect),
            ctypes.sizeof(rect),
        )
        if res == 0:
            return rect.left, rect.top
    except Exception:
        pass
    wl, wt, _wr, _wb = win32gui.GetWindowRect(hwnd)
    return wl, wt


# --------------------------------------------------------------------------- capture backends
class PrintWindowCapture:
    """GDI PrintWindow capture. Synchronous and simple. May return black for some
    GPU-rendered surfaces — the probe verifies this against the real client."""

    def __init__(self, hwnd: int):
        self.hwnd = hwnd

    def capture(self) -> np.ndarray:
        l, t, r, b = win32gui.GetClientRect(self.hwnd)
        w, h = r - l, b - t

        hwnd_dc = win32gui.GetWindowDC(self.hwnd)
        mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
        save_dc = mfc_dc.CreateCompatibleDC()
        bmp = win32ui.CreateBitmap()
        bmp.CreateCompatibleBitmap(mfc_dc, w, h)
        save_dc.SelectObject(bmp)

        try:
            windll.user32.PrintWindow(self.hwnd, save_dc.GetSafeHdc(), PW_RENDERFULLCONTENT)
            info = bmp.GetInfo()
            bits = bmp.GetBitmapBits(True)
            img = np.frombuffer(bits, dtype=np.uint8).reshape(
                (info["bmHeight"], info["bmWidth"], 4)
            )
            return img[:, :, :3].copy()  # BGRA -> BGR
        finally:
            win32gui.DeleteObject(bmp.GetHandle())
            save_dc.DeleteDC()
            mfc_dc.DeleteDC()
            win32gui.ReleaseDC(self.hwnd, hwnd_dc)

    def close(self):
        pass


class WGCCapture:
    """Windows Graphics Capture backend (background-capable, GPU-friendly).

    Uses the `windows-capture` package's free-threaded session and keeps the most recent
    frame so callers can grab on demand. Captures the whole window; we crop to the client
    area so coordinates match the other backend.
    """

    def __init__(self, hwnd: int):
        from windows_capture import WindowsCapture  # imported lazily

        self.hwnd = hwnd
        self._latest: np.ndarray | None = None
        self._control = None

        # Target by HWND (the windows-capture docs note this is more reliable than title,
        # and E7's window title may be empty or non-unique). Leave draw_border unset (None):
        # toggling the capture border isn't supported on all Windows builds and throws
        # ("Toggling the capture border is not supported ... on this platform").
        self._cap = WindowsCapture(
            cursor_capture=False,
            window_hwnd=hwnd,
        )

        @self._cap.event
        def on_frame_arrived(frame, capture_control):  # noqa: ANN001
            # frame_buffer is BGRA, shape (h, w, 4)
            self._latest = frame.frame_buffer[:, :, :3].copy()

        @self._cap.event
        def on_closed():  # noqa: ANN001
            pass

        self._control = self._cap.start_free_threaded()
        # wait for the first frame
        for _ in range(100):
            if self._latest is not None:
                break
            time.sleep(0.02)

    def capture(self) -> np.ndarray:
        if self._latest is None:
            raise RuntimeError("WGC produced no frames; window may be minimized.")
        frame = self._latest
        # WGC returns the whole window incl. borders/title; crop to client area.
        return self._crop_to_client(frame)

    def _crop_to_client(self, frame: np.ndarray) -> np.ndarray:
        # WGC returns the DWM *visible* window bounds (no invisible resize borders), so we
        # must offset against those bounds — NOT GetWindowRect, which includes them.
        cl = win32gui.GetClientRect(self.hwnd)  # (0,0,w,h)
        client_w, client_h = cl[2], cl[3]
        cx, cy = win32gui.ClientToScreen(self.hwnd, (0, 0))

        vl, vt = _visible_window_origin(self.hwnd)
        off_x, off_y = max(0, cx - vl), max(0, cy - vt)

        fh, fw = frame.shape[:2]
        x1 = min(fw, off_x + client_w)
        y1 = min(fh, off_y + client_h)
        return frame[off_y:y1, off_x:x1]

    def close(self):
        try:
            if self._control is not None:
                self._control.stop()
        except Exception:
            pass


# --------------------------------------------------------------------------- input backends
WM_MOUSEWHEEL = 0x020A
WHEEL_DELTA = 120


def _make_lparam(x: int, y: int) -> int:
    return (y << 16) | (x & 0xFFFF)


class PostMessageInput:
    """Background click via PostMessage. No focus required; the game may ignore it."""

    def __init__(self, hwnd: int):
        self.hwnd = hwnd

    def click(self, x: int, y: int):
        lp = _make_lparam(int(x), int(y))
        win32gui.PostMessage(self.hwnd, win32con.WM_MOUSEMOVE, 0, lp)
        time.sleep(0.03)
        win32gui.PostMessage(self.hwnd, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, lp)
        time.sleep(random.uniform(0.04, 0.09))
        win32gui.PostMessage(self.hwnd, win32con.WM_LBUTTONUP, 0, lp)

    def scroll(self, notches: int, x: int, y: int):
        """Wheel scroll at client (x,y). notches<0 scrolls down. WM_MOUSEWHEEL needs
        SCREEN coords in lParam. Many small notches are needed (E7 scrolls a little per notch).
        Screen coords are recomputed each call, so it follows the game window if it's moved."""
        # Hover the list first (client coords) so the game routes the wheel to it — needed
        # after you've interacted with the window.
        win32gui.PostMessage(self.hwnd, win32con.WM_MOUSEMOVE, 0, _make_lparam(int(x), int(y)))
        time.sleep(0.02)
        sx, sy = win32gui.ClientToScreen(self.hwnd, (int(x), int(y)))
        lp = (sy << 16) | (sx & 0xFFFF)
        d = -WHEEL_DELTA if notches < 0 else WHEEL_DELTA
        wp = (d & 0xFFFF) << 16
        for _ in range(abs(int(notches))):
            win32gui.PostMessage(self.hwnd, WM_MOUSEWHEEL, wp, lp)
            time.sleep(0.05)


class ForegroundInput:
    """Real-cursor click. Brings the window forward and uses SendInput via mouse_event.
    The mouse physically moves; the PC is not usable during clicks."""

    def __init__(self, hwnd: int):
        self.hwnd = hwnd

    def click(self, x: int, y: int):
        try:
            win32gui.SetForegroundWindow(self.hwnd)
        except Exception:
            pass
        time.sleep(0.05)
        sx, sy = win32gui.ClientToScreen(self.hwnd, (int(x), int(y)))
        win32api.SetCursorPos((sx, sy))
        time.sleep(random.uniform(0.04, 0.08))
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        time.sleep(random.uniform(0.04, 0.09))
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)

    def scroll(self, notches: int, x: int, y: int):
        sx, sy = win32gui.ClientToScreen(self.hwnd, (int(x), int(y)))
        win32api.SetCursorPos((sx, sy))
        d = -WHEEL_DELTA if notches < 0 else WHEEL_DELTA
        for _ in range(abs(int(notches))):
            win32api.mouse_event(win32con.MOUSEEVENTF_WHEEL, 0, 0, d, 0)
            time.sleep(0.03)


# --------------------------------------------------------------------------- factory
def make_capture(hwnd: int, backend: str):
    if backend == "wgc":
        return WGCCapture(hwnd)
    if backend == "printwindow":
        return PrintWindowCapture(hwnd)
    raise ValueError(f"unknown capture backend: {backend}")


def make_input(hwnd: int, mode: str):
    # background -> PostMessage; hybrid/foreground -> real cursor
    if mode == "background":
        return PostMessageInput(hwnd)
    return ForegroundInput(hwnd)
