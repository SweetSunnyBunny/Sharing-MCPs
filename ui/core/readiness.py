"""Whether the server has finished starting, and when it began."""


import time

_server_start_time = time.time()
_system_ready = False


def set_system_ready(ready: bool) -> None:
    global _system_ready
    _system_ready = ready


def is_system_ready() -> bool:
    return _system_ready


def get_server_start_time() -> float:
    return _server_start_time
