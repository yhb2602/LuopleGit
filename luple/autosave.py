"""Optional per-project background autosave worker, started by the CLI."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from .core import LupleError


def alive(pid):
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.windll.kernel32
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x100000, False, pid)
        if not handle:
            return False
        try:
            return kernel.WaitForSingleObject(handle, 0) == 258
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def start(repo):
    if os.environ.get("LUPLE_NO_WORKER") == "1" or not repo.read()["config"]["autosave_enabled"]:
        return
    marker = repo.directory / "autosave.pid"
    if marker.exists():
        try:
            if alive(int(marker.read_text())):
                return
        except (OSError, ValueError):
            return
        marker.unlink(missing_ok=True)
    command = [sys.executable, str(Path(__file__).resolve().parents[1] / "lu.py"),
               "-C", str(repo.root), "sys", "autosave", "run"]
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
    subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, cwd=repo.root, **options)


def run(repo):
    marker = repo.directory / "autosave.pid"
    try:
        fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return
    os.write(fd, str(os.getpid()).encode())
    os.close(fd)
    try:
        while repo.path.exists():
            try:
                cfg = repo.read()["config"]
                if not cfg["autosave_enabled"]:
                    return
                result = repo.autosave()
                if result:
                    (repo.directory / "autosave.status").write_text("저장 완료 " + result, encoding="utf-8")
                interval = cfg["autosave_interval"]
            except LupleError as error:
                (repo.directory / "autosave.status").write_text(str(error), encoding="utf-8")
                interval = 5
            for _ in range(interval):
                time.sleep(1)
                if not repo.path.exists():
                    return
                cfg = json.loads(repo.path.read_text(encoding="utf-8")).get("config", {})
                if not cfg.get("autosave_enabled", True):
                    return
    finally:
        marker.unlink(missing_ok=True)
