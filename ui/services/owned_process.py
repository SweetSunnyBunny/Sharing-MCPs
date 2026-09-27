"""Windows ownership for an explicitly launched child and its descendants.

The root starts suspended, joins a non-inheritable kill-on-close Job Object,
then resumes. Its exit closes the job even when stdout is held by descendants.
No process-name scans and no idle expiry. A host exit also closes the job handle.
"""
from __future__ import annotations
import ctypes
from ctypes import wintypes
import os
import subprocess
import threading
import psutil

class _BasicLimits(ctypes.Structure):
    _fields_ = [('PerProcessUserTimeLimit',ctypes.c_longlong),('PerJobUserTimeLimit',ctypes.c_longlong),
        ('LimitFlags',wintypes.DWORD),('MinimumWorkingSetSize',ctypes.c_size_t),
        ('MaximumWorkingSetSize',ctypes.c_size_t),('ActiveProcessLimit',wintypes.DWORD),
        ('Affinity',ctypes.c_size_t),('PriorityClass',wintypes.DWORD),('SchedulingClass',wintypes.DWORD)]

class _IoCounters(ctypes.Structure):
    _fields_ = [(name,ctypes.c_ulonglong) for name in ('ReadOperationCount','WriteOperationCount',
        'OtherOperationCount','ReadTransferCount','WriteTransferCount','OtherTransferCount')]

class _ExtendedLimits(ctypes.Structure):
    _fields_ = [('BasicLimitInformation',_BasicLimits),('IoInfo',_IoCounters),
        ('ProcessMemoryLimit',ctypes.c_size_t),('JobMemoryLimit',ctypes.c_size_t),
        ('PeakProcessMemoryUsed',ctypes.c_size_t),('PeakJobMemoryUsed',ctypes.c_size_t)]

class WindowsJob:
    def __init__(self):
        self._lock = threading.Lock()
        self._handle = None
        kernel = ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p,wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD]
        kernel.SetInformationJobObject.restype = wintypes.BOOL
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE,wintypes.HANDLE]
        kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        self._kernel = kernel
        self._handle = kernel.CreateJobObjectW(None,None)
        if not self._handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = _ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel.SetInformationJobObject(self._handle,9,ctypes.byref(limits),ctypes.sizeof(limits)):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def assign(self, proc):
        if not self._kernel.AssignProcessToJobObject(self._handle,int(proc._handle)):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self):
        with self._lock:
            if self._handle:
                handle,self._handle = self._handle,None
                self._kernel.CloseHandle(handle)


def owned_popen(*args, **kwargs):
    if os.name != 'nt':
        return subprocess.Popen(*args, **kwargs)
    job = WindowsJob()
    proc = None
    try:
        kwargs['creationflags'] = kwargs.get('creationflags',0) | 0x4 | subprocess.CREATE_NO_WINDOW
        proc = subprocess.Popen(*args, **kwargs)
        job.assign(proc)
        proc._anam_job = job
        psutil.Process(proc.pid).resume()
        def watch_exit():
            try:
                proc.wait()
            finally:
                job.close()
        threading.Thread(target=watch_exit,daemon=True,name=f'owned-child-{proc.pid}').start()
        return proc
    except BaseException:
        job.close()
        if proc is not None:
            try:
                proc.kill()
                proc.wait(timeout=5)
            except (OSError,subprocess.TimeoutExpired):
                pass
        raise


def close_owned_process(proc):
    """Return True if Windows ownership handled the entire tree, even after root exit."""
    job = getattr(proc,'_anam_job',None)
    if job is None:
        return False
    job.close()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    return True
