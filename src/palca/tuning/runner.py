from __future__ import annotations

from dataclasses import dataclass
import subprocess
import time
from typing import Sequence


@dataclass(frozen=True)
class RunResult:
    ok: bool
    command: list[str]
    json_path: str
    returncode: int
    duration_sec: float
    stdout: str
    stderr: str
    error: str | None = None


def run_sim_subprocess(command: Sequence[str], *, json_path: str, timeout_sec: float) -> RunResult:
    cmd = [str(part) for part in command]
    started = time.perf_counter()
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=max(1.0, float(timeout_sec)),
        )
    except subprocess.TimeoutExpired as exc:
        duration = time.perf_counter() - started
        return RunResult(
            ok=False,
            command=cmd,
            json_path=str(json_path),
            returncode=124,
            duration_sec=float(duration),
            stdout=exc.stdout or "",
            stderr=exc.stderr or "",
            error=f"timeout_after_{max(1.0, float(timeout_sec)):.2f}s",
        )

    duration = time.perf_counter() - started
    ok = proc.returncode == 0
    error = None if ok else f"returncode_{proc.returncode}"
    return RunResult(
        ok=ok,
        command=cmd,
        json_path=str(json_path),
        returncode=int(proc.returncode),
        duration_sec=float(duration),
        stdout=proc.stdout or "",
        stderr=proc.stderr or "",
        error=error,
    )
