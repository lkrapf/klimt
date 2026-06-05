"""Shell-execution helpers shared by `bash` and `grep` tools."""
from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from pathlib import Path
from threading import Event

from .limits import BASH_TIMEOUT


def format_result(returncode: int | str, stdout: str, stderr: str) -> str:
    return (
        f"exit={returncode}\n"
        f"--- stdout ---\n{stdout}"
        f"--- stderr ---\n{stderr}"
    )


def kill_process_tree(p: "subprocess.Popen[str]") -> None:
    try:
        os.killpg(p.pid, signal.SIGTERM)
    except Exception:
        p.terminate()

    try:
        p.wait(timeout=2)
        return
    except subprocess.TimeoutExpired:
        pass

    try:
        os.killpg(p.pid, signal.SIGKILL)
    except Exception:
        p.kill()


def bash(command: str, cancel: Event | None = None, cwd: str | None = None) -> str:
    workdir = Path(cwd or os.getcwd()).expanduser()
    if not workdir.exists() or not workdir.is_dir():
        return f"error: cwd is not a directory: {workdir}"
    p = subprocess.Popen(
        command,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
        cwd=str(workdir),
    )
    # communicate() in a background thread so it drains stdout/stderr
    # continuously. A polling loop with p.poll() + late communicate()
    # deadlocks when pipe buffers fill up before the process exits.
    io_result: list[tuple[str, str] | None] = [None]

    def _read() -> None:
        io_result[0] = p.communicate()

    t = threading.Thread(target=_read, daemon=True)
    t.start()

    deadline = time.monotonic() + BASH_TIMEOUT

    while t.is_alive():
        if cancel and cancel.is_set():
            kill_process_tree(p)
            t.join()
            stdout, stderr = io_result[0] or ("", "")
            return format_result("interrupted", stdout, stderr)
        if time.monotonic() >= deadline:
            kill_process_tree(p)
            t.join()
            stdout, stderr = io_result[0] or ("", "")
            return format_result("timeout", stdout, stderr)
        time.sleep(0.1)

    stdout, stderr = io_result[0] or ("", "")
    return format_result(p.returncode, stdout, stderr)
