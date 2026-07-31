#!/usr/bin/env python3
"""Build the WebUI and the platform-native PyInstaller Tauri sidecar."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
BACKEND = ROOT / "backend"
BINARIES = FRONTEND / "src-tauri" / "binaries"
SIDECAR_NAME = "codex-cockpit-backend"


def run(command: list[str], cwd: Path = ROOT) -> None:
    print(f"+ {' '.join(command)}", flush=True)
    subprocess.run(command, cwd=cwd, check=True)


def executable(name: str) -> str:
    result = shutil.which(name)
    if result is None:
        raise SystemExit(f"缺少构建工具: {name}")
    return result


def main() -> None:
    npm = executable("npm")
    uv = executable("uv")
    rustc = executable("rustc")

    run([npm, "run", "build"], FRONTEND)
    run([uv, "sync", "--locked"], BACKEND)

    target = subprocess.run(
        [rustc, "--print", "host-tuple"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if not target:
        raise SystemExit("rustc 未返回 host tuple")

    BINARIES.mkdir(parents=True, exist_ok=True)
    suffix = ".exe" if os.name == "nt" else ""
    output_name = f"{SIDECAR_NAME}-{target}"
    add_data = f"{FRONTEND / 'dist'}{os.pathsep}frontend/dist"
    work_dir = BACKEND / "build" / "pyinstaller"
    spec_dir = BACKEND / "build" / "spec"
    work_dir.mkdir(parents=True, exist_ok=True)
    spec_dir.mkdir(parents=True, exist_ok=True)

    run(
        [
            uv,
            "run",
            "pyinstaller",
            "--noconfirm",
            "--clean",
            "--onefile",
            "--name",
            output_name,
            "--distpath",
            str(BINARIES),
            "--workpath",
            str(work_dir),
            "--specpath",
            str(spec_dir),
            "--add-data",
            add_data,
            str(BACKEND / "sidecar_entry.py"),
        ],
        BACKEND,
    )

    output = BINARIES / f"{output_name}{suffix}"
    if not output.is_file():
        raise SystemExit(f"sidecar 构建产物不存在: {output}")
    print(f"Sidecar: {output}", flush=True)


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as error:
        sys.exit(error.returncode)
