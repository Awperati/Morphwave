"""Windows helpers: DPI awareness, live-wallpaper embedding and global hotkeys."""

import ctypes
import threading
from ctypes import wintypes

user32 = ctypes.windll.user32
EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

user32.FindWindowW.restype = wintypes.HWND
user32.FindWindowExW.restype = wintypes.HWND
user32.FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.SetParent.restype = wintypes.HWND
user32.SetParent.argtypes = [wintypes.HWND, wintypes.HWND]
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
user32.GetParent.restype = wintypes.HWND
user32.GetParent.argtypes = [wintypes.HWND]
user32.GetForegroundWindow.restype = wintypes.HWND
user32.SendMessageTimeoutW.argtypes =[wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
                                       wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]

GWL_STYLE = -16
GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_CHILD = 0x40000000
WS_POPUP = 0x80000000
WS_VISIBLE = 0x10000000
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020
SWP_SHOWWINDOW = 0x0040


def make_dpi_aware():
    try:
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # per-monitor v2
    except Exception:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass


def _find_wallpaper_host():
    """Return the WorkerW window that sits behind the desktop icons."""
    progman = user32.FindWindowW("Progman", None)
    if not progman:
        return None
    res = ctypes.c_size_t()
    # Ask Explorer to create the WorkerW layer used by live wallpapers.
    user32.SendMessageTimeoutW(progman, 0x052C, 0xD, 0x1, 0, 1000, ctypes.byref(res))
    user32.SendMessageTimeoutW(progman, 0x052C, 0, 0, 0, 1000, ctypes.byref(res))

    # Windows 11 24H2 and later: WorkerW is a child of Progman, behind the icon view.
    host = user32.FindWindowExW(progman, None, "WorkerW", None)
    if host:
        return host

    # Older builds: the top-level WorkerW right after the one holding the icons.
    found = []

    def cb(hwnd, _):
        if user32.FindWindowExW(hwnd, None, "SHELLDLL_DefView", None):
            w = user32.FindWindowExW(None, hwnd, "WorkerW", None)
            if w:
                found.append(w)
        return True

    user32.EnumWindows(EnumWindowsProc(cb), 0)
    return found[0] if found else None


def embed_as_wallpaper(hwnd, x, y, w, h):
    """Re-parent our window behind the desktop icons, covering one monitor.

    x, y are the monitor's position in virtual-screen coordinates.
    Returns True on success.

    Windows 11 24H2 and later changed the desktop: Progman now holds the icon view
    (SHELLDLL_DefView) and a WorkerW as children, and a plain child of that WorkerW
    is never drawn. There the window must be a layered child of Progman itself,
    slotted between the icons (above) and the WorkerW (below).
    """
    progman = user32.FindWindowW("Progman", None)
    host = _find_wallpaper_host()
    if not host:
        return False
    modern = user32.GetParent(host) == progman
    parent = progman if modern else host
    style = user32.GetWindowLongPtrW(hwnd, GWL_STYLE)
    user32.SetWindowLongPtrW(hwnd, GWL_STYLE, (style & ~WS_POPUP) | WS_CHILD | WS_VISIBLE)
    user32.SetParent(hwnd, parent)
    # The layered flag must be set AFTER re-parenting; set before, Windows never draws the child.
    ex = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
    user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, ex | WS_EX_LAYERED)
    user32.SetLayeredWindowAttributes(hwnd, 0, 255, 0x2)              # LWA_ALPHA, fully opaque
    pr = _RECT()
    user32.GetWindowRect(parent, ctypes.byref(pr))                   # parent's top-left in screen coordinates
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
    user32.SetWindowPos(hwnd, None, x - pr.left, y - pr.top, w, h,
                        SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED | SWP_SHOWWINDOW)
    if modern:
        restack(hwnd)
    return user32.GetParent(hwnd) == parent


def restack(hwnd):
    """Keep our window between the desktop icons (above) and the old wallpaper layer (below)."""
    progman = user32.FindWindowW("Progman", None)
    if user32.GetParent(hwnd) != progman:
        return
    defview = user32.FindWindowExW(progman, None, "SHELLDLL_DefView", None)
    worker = user32.FindWindowExW(progman, None, "WorkerW", None)
    flags = 0x0001 | 0x0002 | SWP_NOACTIVATE                         # SWP_NOSIZE | SWP_NOMOVE
    if defview:
        user32.SetWindowPos(defview, 0, 0, 0, 0, 0, flags)            # icons to the top of Progman's children
        user32.SetWindowPos(hwnd, defview, 0, 0, 0, 0, flags)         # us just below them
    if worker:
        user32.SetWindowPos(worker, hwnd, 0, 0, 0, 0, flags)          # old wallpaper below us


def release_wallpaper(hwnd):
    """Detach from the desktop and make Explorer repaint the normal wallpaper.

    Only asks for a repaint; your wallpaper settings are never changed.
    """
    host = user32.GetParent(hwnd)
    try:
        user32.ShowWindow(hwnd, 0)
        user32.SetParent(hwnd, None)
    except Exception:
        pass
    flags = 0x0001 | 0x0004 | 0x0080 | 0x0100  # RDW_INVALIDATE | RDW_ERASE | RDW_ALLCHILDREN | RDW_UPDATENOW
    for w in (host, user32.FindWindowW("Progman", None)):
        if w:
            user32.RedrawWindow(w, None, None, flags)


def unembed_to_popup(hwnd, x, y, w, h):
    """Turn an embedded wallpaper window back into a borderless full-screen window."""
    user32.ShowWindow(hwnd, 0)
    user32.SetParent(hwnd, None)
    style = user32.GetWindowLongPtrW(hwnd, GWL_STYLE)
    user32.SetWindowLongPtrW(hwnd, GWL_STYLE, (style & ~WS_CHILD) | WS_POPUP | WS_VISIBLE)
    ex = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
    user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, ex & ~WS_EX_LAYERED)
    user32.SetWindowPos(hwnd, None, x, y, w, h, SWP_NOZORDER | SWP_FRAMECHANGED | SWP_SHOWWINDOW)
    user32.SetForegroundWindow(hwnd)


def set_window_icon(hwnd, ico_path):
    """Give a window the .ico for its title bar, taskbar button and Alt+Tab (big and small sizes)."""
    LoadImageW = user32.LoadImageW
    LoadImageW.restype = wintypes.HANDLE
    LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT, ctypes.c_int, ctypes.c_int, wintypes.UINT]
    user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.SetClassLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    LR_LOADFROMFILE, IMAGE_ICON, WM_SETICON = 0x10, 1, 0x80
    big = LoadImageW(None, str(ico_path), IMAGE_ICON, user32.GetSystemMetrics(11), user32.GetSystemMetrics(12), LR_LOADFROMFILE)
    small = LoadImageW(None, str(ico_path), IMAGE_ICON, user32.GetSystemMetrics(49), user32.GetSystemMetrics(50), LR_LOADFROMFILE)
    if big:
        user32.SendMessageW(hwnd, WM_SETICON, 1, big)
        user32.SetClassLongPtrW(hwnd, -14, big)          # GCLP_HICON
    if small:
        user32.SendMessageW(hwnd, WM_SETICON, 0, small)
        user32.SetClassLongPtrW(hwnd, -34, small)        # GCLP_HICONSM
    return bool(big and small)


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def fullscreen_app_on(x, y, w, h, own_hwnd):
    """True when some other program (a game, a video) fills this monitor."""
    fg = user32.GetForegroundWindow()
    if not fg or fg == own_hwnd:
        return False
    cls = ctypes.create_unicode_buffer(64)
    user32.GetClassNameW(fg, cls, 64)
    if cls.value in ("Progman", "WorkerW", "Shell_TrayWnd"):
        return False
    r = _RECT()
    user32.GetWindowRect(fg, ctypes.byref(r))
    return r.left <= x and r.top <= y and r.right >= x + w and r.bottom >= y + h


def dwm_flush():
    """Block until the desktop compositor's next frame."""
    try:
        ctypes.windll.dwmapi.DwmFlush()
    except OSError:
        pass


def _cloaked(hwnd):
    """Windows hides some apps (suspended Store apps, other virtual desktops) by cloaking them."""
    val = ctypes.c_int(0)
    try:
        ctypes.windll.dwmapi.DwmGetWindowAttribute(wintypes.HWND(hwnd), 14, ctypes.byref(val), ctypes.sizeof(val))
    except OSError:
        return False
    return val.value != 0


def covered(x, y, w, h, own_hwnd, stop_at_own=False):
    """True when other windows hide (nearly) all of this monitor, so nobody can see us.

    Catches maximised windows too, which leave the taskbar strip uncovered.
    Windows are checked from the top of the z-order down; with stop_at_own, only
    the ones above our own window count (fullscreen mode).
    """
    WS_EX_TOOLWINDOW, WS_EX_TRANSPARENT, WS_EX_NOACTIVATE = 0x80, 0x20, 0x08000000
    found = [False]
    cls = ctypes.create_unicode_buffer(64)

    def visit(hwnd, _):
        if hwnd == own_hwnd:
            return not stop_at_own                       # windows below ours cannot hide us
        if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd) or _cloaked(hwnd):
            return True
        ex = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
        if ex & (WS_EX_TOOLWINDOW | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE):   # overlays, tool palettes
            return True
        user32.GetClassNameW(hwnd, cls, 64)
        if cls.value in ("Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
            return True
        r = _RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        ix = max(0, min(r.right, x + w) - max(r.left, x))
        iy = max(0, min(r.bottom, y + h) - max(r.top, y))
        if ix * iy >= 0.85 * w * h:
            found[0] = True
            return False
        return True

    user32.EnumWindows(EnumWindowsProc(visit), 0)
    return found[0]


class GlobalHotkeys(threading.Thread):
    """System-wide shortcuts, so the app can be controlled while it is the wallpaper.

    bindings: list of (modifiers, virtual_key, callback).
    """

    MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x4000

    def __init__(self, bindings):
        super().__init__(daemon=True, name="hotkeys")
        self.bindings = bindings
        self.failed = []

    def run(self):
        for i, (mods, vk, _) in enumerate(self.bindings):
            if not user32.RegisterHotKey(None, i + 1, mods | self.MOD_NOREPEAT, vk):
                self.failed.append(i)
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == 0x0312:  # WM_HOTKEY
                idx = msg.wParam - 1
                if 0 <= idx < len(self.bindings):
                    try:
                        self.bindings[idx][2]()
                    except Exception:
                        pass


class _MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", wintypes.POINT), ("mouseData", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


_HookProc = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, _HookProc, wintypes.HINSTANCE, wintypes.DWORD]
user32.CallNextHookEx.restype = ctypes.c_ssize_t
user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.WindowFromPoint.restype = wintypes.HWND
user32.WindowFromPoint.argtypes = [wintypes.POINT]
user32.GetAncestor.restype = wintypes.HWND
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
user32.MsgWaitForMultipleObjects.argtypes = [wintypes.DWORD, ctypes.c_void_p, wintypes.BOOL, wintypes.DWORD, wintypes.DWORD]


def _is_desktop(pt):
    """True when the point is over the bare desktop (icons layer or wallpaper), not a window."""
    h = user32.WindowFromPoint(pt)
    if not h:
        return False
    root = user32.GetAncestor(h, 2)  # GA_ROOT
    cls = ctypes.create_unicode_buffer(32)
    user32.GetClassNameW(root, cls, 32)
    return cls.value in ("Progman", "WorkerW")


class DesktopMouseHook(threading.Thread):
    """Forwards mouse moves and clicks on the desktop to the wallpaper.

    callback(kind, x, y) with kind "move", "left" or "right" and screen coordinates;
    it returns True to swallow a click (so the desktop doesn't also react to it).
    Only events over the bare desktop are forwarded; normal windows are never touched.
    """

    def __init__(self, callback, wanted):
        super().__init__(daemon=True, name="desktop-mouse")
        self.callback, self.wanted = callback, wanted
        self.installed = False
        self._swallow_up = set()

    def _proc(self, n_code, w_param, l_param):
        try:
            if n_code == 0:
                info = ctypes.cast(l_param, ctypes.POINTER(_MSLLHOOKSTRUCT)).contents
                pt = info.pt
                if w_param in (0x0202, 0x0205) and w_param in self._swallow_up:   # button up after a swallowed down
                    self._swallow_up.discard(w_param)
                    return 1
                if w_param in (0x0200, 0x0201, 0x0204) and _is_desktop(pt):
                    kind = {0x0200: "move", 0x0201: "left", 0x0204: "right"}[w_param]
                    if self.callback(kind, pt.x, pt.y) and kind != "move":
                        self._swallow_up.add(w_param + 1)
                        return 1
        except Exception:
            pass
        return user32.CallNextHookEx(None, n_code, w_param, l_param)

    def run(self):
        """Install the hook only while wanted() is true (we are the visible wallpaper).

        While a game or any normal window is in front, no hook is installed at all,
        so system-wide mouse input never passes through Python.
        """
        self._cb = _HookProc(self._proc)  # keep a reference so it isn't garbage-collected
        hook = None
        msg = wintypes.MSG()
        while True:
            want = self.wanted()
            if want and not hook:
                hook = user32.SetWindowsHookExW(14, self._cb, None, 0)  # WH_MOUSE_LL
                self.installed = bool(hook)
            elif not want and hook:
                user32.UnhookWindowsHookEx(hook)
                hook = None
                self.installed = False
            # wake at once for hook messages, otherwise re-check every half second
            user32.MsgWaitForMultipleObjects(0, None, False, 500, 0x04FF)  # QS_ALLINPUT
            while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):   # PM_REMOVE
                pass
