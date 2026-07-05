"""Background RAM/CPU/GPU usage logging for the assistant process."""
import logging
import os
import subprocess
import threading

import psutil

from jetson_assistant.log_fmt import info as log_line

log = logging.getLogger("assistant.resources")


def _gpu_summary() -> str | None:
    """System GPU via nvidia-smi, or this process via torch CUDA."""
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=utilization.gpu,memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            util, used, total = [part.strip() for part in result.stdout.split(",", 2)]
            return f"GPU {util}% ({used}/{total} MB)"
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
        pass

    try:
        import torch

        if torch.cuda.is_available():
            alloc = torch.cuda.memory_allocated() / (1024 * 1024)
            reserved = torch.cuda.memory_reserved() / (1024 * 1024)
            return f"CUDA {alloc:.0f}/{reserved:.0f} MB alloc/reserved"
    except Exception:
        pass
    return None


def _usage_line() -> str:
    proc = psutil.Process(os.getpid())
    rss_mb = proc.memory_info().rss / (1024 * 1024)
    cpu_pct = proc.cpu_percent(interval=None)
    vm = psutil.virtual_memory()
    parts = [
        f"proc RAM {rss_mb:.0f} MB",
        f"CPU {cpu_pct:.0f}%",
        f"system RAM {vm.percent:.0f}%",
    ]
    gpu = _gpu_summary()
    if gpu:
        parts.append(gpu)
    return ", ".join(parts)


class ResourceMonitor:
    """Log resource usage on a fixed interval in a daemon thread."""

    def __init__(self, interval_seconds: float) -> None:
        self._interval = interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        psutil.Process(os.getpid()).cpu_percent(interval=None)
        self._thread = threading.Thread(target=self._loop, name="resource-monitor", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                log_line(log, "Resources", _usage_line())
            except Exception as exc:
                log_line(log, "Resources", f"unavailable ({exc})")

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)
