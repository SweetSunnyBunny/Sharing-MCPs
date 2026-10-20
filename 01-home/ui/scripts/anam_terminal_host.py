"""Own one ConPTY command and normalize inherited Windows Ctrl+C handling."""
import base64
import ctypes
import signal
import subprocess
import sys


def main():
    # The console's ignore-Ctrl+C flag is inherited (including from test/service
    # hosts). Reset it inside our own console before spawning the actual command.
    ctypes.windll.kernel32.SetConsoleCtrlHandler(None, False)
    # The host waits while the command handles Ctrl+C; don't abandon its children.
    signal.signal(signal.SIGINT, lambda *_: None)
    shell, encoded = sys.argv[1:3]
    if shell == "powershell":
        command = ["powershell.exe", "-NoLogo", "-NoProfile", "-EncodedCommand", encoded]
    else:
        text = base64.b64decode(encoded).decode("utf-16-le")
        command = 'cmd.exe /d /s /c "' + text + '"'
    code = subprocess.call(command)
    # sys.exit uses a signed C long; preserve Windows' 32-bit status bits.
    return code if code < 2**31 else code - 2**32


if __name__ == "__main__":
    sys.exit(main())
