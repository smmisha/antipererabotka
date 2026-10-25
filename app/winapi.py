"""Windows через ctypes: простой, мониторы, клавиатурный хук, сон, mutex, съёмные диски."""

from __future__ import annotations

import ctypes
import os
import threading
from ctypes import wintypes
from pathlib import Path

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
powrprof = ctypes.WinDLL("powrprof", use_last_error=True)
shcore = ctypes.WinDLL("shcore", use_last_error=True)

KEY_FILE = "antipererabotka.key"
DRIVE_REMOVABLE = 2
ERROR_ALREADY_EXISTS = 183
WH_KEYBOARD_LL = 13
WM_QUIT = 0x0012
WM_SYSCOMMAND = 0x0112
SC_MONITORPOWER = 0xF170
HWND_BROADCAST = 0xFFFF
LLKHF_ALTDOWN = 0x20
VK_TAB = 0x09
VK_ESCAPE = 0x1B
VK_F4 = 0x73
VK_CONTROL = 0x11
VK_LWIN = 0x5B
VK_RWIN = 0x5C

HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
MONITORENUMPROC = ctypes.WINFUNCTYPE(
    wintypes.BOOL, wintypes.HANDLE, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM
)

kernel32.GetTickCount64.restype = ctypes.c_ulonglong
kernel32.GetModuleHandleW.restype = wintypes.HANDLE
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.CreateMutexW.restype = wintypes.HANDLE
kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.GetDriveTypeW.restype = wintypes.UINT
kernel32.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.SetWindowsHookExW.restype = wintypes.HANDLE
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HANDLE, wintypes.DWORD]
user32.CallNextHookEx.restype = ctypes.c_ssize_t
user32.CallNextHookEx.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.UnhookWindowsHookEx.argtypes = [wintypes.HANDLE]
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
powrprof.SetSuspendState.argtypes = [wintypes.BOOL, wintypes.BOOL, wintypes.BOOL]


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


class _KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


def set_dpi_aware() -> None:
    """Иначе на мониторах с масштабированием окна блокировки ложатся криво."""
    try:
        shcore.SetProcessDpiAwareness(2)
    except (OSError, AttributeError):
        user32.SetProcessDPIAware()


def tick_ms() -> int:
    """GetTickCount64 идёт и во время сна, от часов не зависит."""
    return int(kernel32.GetTickCount64())


def idle_seconds() -> float:
    info = _LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(info)
    if not user32.GetLastInputInfo(ctypes.byref(info)):
        raise ctypes.WinError(ctypes.get_last_error())
    now32 = kernel32.GetTickCount64() & 0xFFFFFFFF
    return ((now32 - info.dwTime) & 0xFFFFFFFF) / 1000


def monitor_rects() -> list[tuple[int, int, int, int]]:
    """Список (x, y, ширина, высота) для каждого монитора."""
    rects: list[tuple[int, int, int, int]] = []

    def collect(_monitor, _dc, rect, _data):
        r = rect.contents
        rects.append((r.left, r.top, r.right - r.left, r.bottom - r.top))
        return True

    proc = MONITORENUMPROC(collect)
    user32.EnumDisplayMonitors(None, None, proc, 0)
    return rects


def removable_roots(extra: list[str]) -> set[str]:
    """Корни съёмных дисков вида E:\\. extra — папки, которые считаются съёмными (только для --test)."""
    roots: set[str] = set()
    mask = kernel32.GetLogicalDrives()
    for i in range(26):
        if mask & (1 << i):
            root = f"{chr(ord('A') + i)}:\\"
            if kernel32.GetDriveTypeW(root) == DRIVE_REMOVABLE:
                roots.add(root)
    return roots | {os.path.join(folder, "") for folder in extra if os.path.isdir(folder)}


def read_key(root: str) -> str | None:
    try:
        return (Path(root) / KEY_FILE).read_text(encoding="utf-8")
    except OSError:
        return None


def write_key(root: str, secret: str) -> Path:
    path = Path(root) / KEY_FILE
    with open(path, "w", encoding="utf-8") as f:
        f.write(secret + "\n")
        f.flush()
        os.fsync(f.fileno())
    return path


def suspend() -> bool:
    """Сон. False — система не перешла в сон (например, Modern Standby без S3)."""
    return bool(powrprof.SetSuspendState(0, 0, 0))


def monitor_off() -> None:
    user32.SendMessageW(HWND_BROADCAST, WM_SYSCOMMAND, SC_MONITORPOWER, 2)


def foreground(hwnd: int) -> None:
    user32.SetForegroundWindow(hwnd)


def single_instance(name: str):
    """Вернёт handle, который надо держать живым. None — уже запущен другой экземпляр."""
    handle = kernel32.CreateMutexW(None, False, name)
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        return None
    return handle


def _blocked(vk: int, flags: int) -> bool:
    if vk in (VK_LWIN, VK_RWIN):
        return True
    alt = bool(flags & LLKHF_ALTDOWN)
    ctrl = bool(user32.GetAsyncKeyState(VK_CONTROL) & 0x8000)
    if alt and vk in (VK_TAB, VK_F4, VK_ESCAPE):
        return True
    return ctrl and vk == VK_ESCAPE


class KeyboardGuard:
    """Блокирует Win, Alt+Tab, Alt+F4, Alt+Esc, Ctrl+Esc. Работает в своём потоке с циклом сообщений."""

    def __init__(self) -> None:
        self._proc = HOOKPROC(self._hook)  # ссылку держим всё время, иначе хук упадёт
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._ready = threading.Event()

    @property
    def active(self) -> bool:
        return self._thread is not None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._ready.clear()
        self._thread = threading.Thread(target=self._run, name="keyboard-guard", daemon=True)
        self._thread.start()
        self._ready.wait(5)

    def stop(self) -> None:
        if self._thread is None:
            return
        user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        self._thread.join(5)
        self._thread = None

    def _hook(self, n_code, w_param, l_param):
        if n_code == 0:
            info = ctypes.cast(l_param, ctypes.POINTER(_KBDLLHOOKSTRUCT)).contents
            if _blocked(info.vkCode, info.flags):
                return 1
        return user32.CallNextHookEx(None, n_code, w_param, l_param)

    def _run(self) -> None:
        self._thread_id = kernel32.GetCurrentThreadId()
        hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, kernel32.GetModuleHandleW(None), 0)
        self._ready.set()
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            pass
        if hook:
            user32.UnhookWindowsHookEx(hook)
