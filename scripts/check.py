#!/usr/bin/env python3
"""Run the repository's reproducible local quality gate."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
TAURI = FRONTEND / "src-tauri"


def executable(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise SystemExit(f"缺少检查工具: {name}")
    return path


def run(command: list[str], cwd: Path) -> None:
    print(f"\n+ {' '.join(command)}", flush=True)
    subprocess.run(command, cwd=cwd, check=True)


def main() -> None:
    uv = executable("uv")
    npm = executable("npm")
    cargo = executable("cargo")

    python_paths = ["src", "tests", "sidecar_entry.py", "../scripts"]
    for command in (
        [uv, "sync", "--locked"],
        [uv, "run", "ruff", "format", "--check", *python_paths],
        [uv, "run", "ruff", "check", *python_paths],
        [uv, "run", "pytest"],
    ):
        run(command, BACKEND)

    for command in (
        [npm, "ci"],
        [npm, "run", "format:check"],
        [npm, "run", "lint"],
        [npm, "run", "check"],
        [npm, "test"],
        [npm, "run", "build"],
    ):
        run(command, FRONTEND)

    run(["python3", str(ROOT / "scripts" / "build_sidecar.py")], ROOT)
    run(["python3", str(ROOT / "scripts" / "smoke_sidecar.py")], ROOT)

    for command in (
        [cargo, "fmt", "--all", "--", "--check"],
        [cargo, "clippy", "--all-targets", "--locked", "--", "-D", "warnings"],
        [cargo, "test", "--locked"],
    ):
        run(command, TAURI)

    print("\nAll checks passed.")


if __name__ == "__main__":
    main()
