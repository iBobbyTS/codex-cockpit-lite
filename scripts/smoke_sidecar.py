#!/usr/bin/env python3
"""Smoke-test the bundled backend executable and its dynamic port behavior."""

from __future__ import annotations

import argparse
import json
import os
import queue
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BINARIES = ROOT / "frontend" / "src-tauri" / "binaries"


def executable(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise SystemExit(f"缺少测试工具: {name}")
    return path


def sidecar_path() -> Path:
    target = subprocess.run(
        [executable("rustc"), "--print", "host-tuple"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    suffix = ".exe" if os.name == "nt" else ""
    return BINARIES / f"codex-cockpit-backend-{target}{suffix}"


def read_port(process: subprocess.Popen[str], timeout: float = 15) -> int:
    assert process.stdout is not None
    lines: queue.Queue[str] = queue.Queue()

    def reader() -> None:
        for line in process.stdout:
            lines.put(line)

    threading.Thread(target=reader, daemon=True).start()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stderr = process.stderr.read() if process.stderr else ""
            raise RuntimeError(f"sidecar 提前退出 ({process.returncode}): {stderr}")
        try:
            line = lines.get(timeout=0.1).strip()
        except queue.Empty:
            continue
        if line.startswith("PORT="):
            return int(line.removeprefix("PORT="))
    raise TimeoutError("等待 sidecar PORT 协议超时")


def fetch(url: str) -> tuple[int, bytes, str]:
    with urllib.request.urlopen(url, timeout=5) as response:
        return response.status, response.read(), response.headers.get_content_type()


def assert_port_released(port: int) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.2)
            if probe.connect_ex(("127.0.0.1", port)) != 0:
                return
        time.sleep(0.1)
    raise AssertionError(f"sidecar 退出后端口 {port} 仍被占用")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--binary",
        type=Path,
        help="Explicit sidecar path, including the final Tauri-bundled executable",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    binary = args.binary.resolve() if args.binary else sidecar_path()
    if not binary.is_file():
        raise SystemExit(f"sidecar 不存在, 请先构建: {binary}")

    with tempfile.TemporaryDirectory(prefix="codex-cockpit-smoke-") as temp_dir:
        config_dir = Path(temp_dir)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as occupied:
            occupied.bind(("127.0.0.1", 0))
            occupied.listen(1)
            requested_port = occupied.getsockname()[1]
            config = {
                "version": 1,
                "api": {
                    "port": requested_port,
                    "bind_host": "127.0.0.1",
                    "speed": "standard",
                    "selected_accounts": [],
                    "auto_switch": {
                        "enabled": True,
                        "strategy": "sequential",
                        "quota_threshold_percent": 95,
                    },
                },
            }
            (config_dir / "config.json").write_text(json.dumps(config))
            process = subprocess.Popen(
                [str(binary), "--config-dir", str(config_dir)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            actual_port = 0
            try:
                actual_port = read_port(process)
                assert actual_port > requested_port

                base = f"http://127.0.0.1:{actual_port}"
                status_code, status_body, _ = fetch(f"{base}/v1/cockpit/status")
                assert status_code == 200
                assert json.loads(status_body)["actual_port"] == actual_port

                _, config_body, _ = fetch(f"{base}/api/config")
                assert json.loads(config_body)["api"]["port"] == actual_port

                root_status, root_body, content_type = fetch(f"{base}/")
                assert root_status == 200
                assert content_type == "text/html"
                assert b"Codex Cockpit Lite" in root_body
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        if actual_port:
            assert_port_released(actual_port)
        print(f"Sidecar smoke test passed on dynamic port {actual_port}")


if __name__ == "__main__":
    main()
