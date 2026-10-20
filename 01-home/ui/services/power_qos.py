"""Never let Windows park us on the efficiency cores."""
import ctypes
import ctypes.wintypes  # noqa: F401  (not auto-loaded by `import ctypes`)
import logging
import os
import sys

log = logging.getLogger(__name__)

_PROCESS_SET_INFORMATION = 0x0200
_ProcessPowerThrottling = 4
_EXECUTION_SPEED = 0x1
_IGNORE_TIMER_RESOLUTION = 0x4


def never_throttle(pid: int | None = None, label: str = "") -> bool:
    """Tell Windows this process must run at full speed. Returns True if applied."""
    if sys.platform != "win32":
        return False
    pid = pid or os.getpid()
    try:
        k = ctypes.windll.kernel32
        w = ctypes.wintypes

        class PowerThrottlingState(ctypes.Structure):
            _fields_ = [("Version", w.ULONG), ("ControlMask", w.ULONG), ("StateMask", w.ULONG)]

        handle = k.OpenProcess(_PROCESS_SET_INFORMATION, False, int(pid))
        if not handle:
            log.debug("never_throttle: cannot open pid %s (err %s)", pid, k.GetLastError())
            return False
        try:
            state = PowerThrottlingState(1, _EXECUTION_SPEED | _IGNORE_TIMER_RESOLUTION, 0)
            ok = bool(k.SetProcessInformation(handle, _ProcessPowerThrottling,
                                              ctypes.byref(state), ctypes.sizeof(state)))
        finally:
            k.CloseHandle(handle)
        if ok:
            log.info("never_throttle: %s(pid %s) exempt from Windows efficiency mode", label or "", pid)
        else:
            log.debug("never_throttle: refused for pid %s (err %s)", pid, k.GetLastError())
        return ok
    except Exception as exc:
        log.debug("never_throttle: %s", exc)
        return False
