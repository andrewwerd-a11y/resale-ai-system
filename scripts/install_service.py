"""
Install the Resale AI System as a Windows startup task.

Run once:      uv run python scripts/install_service.py
Uninstall:     uv run python scripts/install_service.py --uninstall

After install, the server starts automatically when you log in to Windows.
Open http://localhost:8000 once the task has run (~10 seconds after login).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
TASK_NAME = "ResaleAISystem"


def install() -> None:
    # The command to run — uses uv so the venv is activated automatically
    run_cmd = (
        f'cmd /c "cd /d {PROJECT_DIR} && '
        f'uv run uvicorn apps.api.src.main:app --host 127.0.0.1 --port 8000"'
    )
    cmd = [
        "schtasks", "/create",
        "/tn", TASK_NAME,
        "/tr", run_cmd,
        "/sc", "ONLOGON",
        "/rl", "HIGHEST",   # run with current user's highest privilege level
        "/f",               # force overwrite if already exists
    ]
    try:
        subprocess.run(cmd, check=True)
        print(f"✓ Task '{TASK_NAME}' created.")
        print("  The server will start automatically on your next login.")
        print("  → http://localhost:8000")
        print()
        print("  To start now without logging out:")
        print(f"    schtasks /run /tn {TASK_NAME}")
    except subprocess.CalledProcessError as e:
        print(f"✗ Failed to create task: {e}")
        print("  Try running this script as Administrator.")
        sys.exit(1)


def uninstall() -> None:
    cmd = ["schtasks", "/delete", "/tn", TASK_NAME, "/f"]
    try:
        subprocess.run(cmd, check=True)
        print(f"✓ Task '{TASK_NAME}' removed. Auto-start disabled.")
    except subprocess.CalledProcessError as e:
        print(f"✗ Failed to remove task: {e}")
        sys.exit(1)


if __name__ == "__main__":
    if "--uninstall" in sys.argv:
        uninstall()
    else:
        install()
