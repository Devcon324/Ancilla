"""Console-script entrypoint for `uv run ancilla`."""
from __future__ import annotations

import os
import sys
import threading


def _quiet_onnxruntime_drm_warnings() -> None:
    """
    ONNX Runtime (via openWakeWord / Piper) probes /sys/class/drm/*/device/vendor
    for discrete GPUs. On Jetson Tegra those nodes are missing or unreadable, so
    it prints harmless WARNING lines to C stderr on import. Filter them out.
    """
    try:
        r_fd, w_fd = os.pipe()
        real_stderr = os.dup(2)
        os.dup2(w_fd, 2)
        os.close(w_fd)
    except OSError:
        return

    def _filter() -> None:
        suppress = (b"device_discovery.cc", b"GetGpuDevices")
        with os.fdopen(r_fd, "rb", buffering=0) as inp, os.fdopen(
            real_stderr, "wb", buffering=0
        ) as out:
            buf = b""
            while True:
                chunk = inp.read(4096)
                if not chunk:
                    if buf and not any(s in buf for s in suppress):
                        out.write(buf)
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line += b"\n"
                    if any(s in line for s in suppress):
                        continue
                    out.write(line)

    threading.Thread(target=_filter, daemon=True).start()


def main() -> None:
    _quiet_onnxruntime_drm_warnings()
    from ancilla.main import run

    run()
