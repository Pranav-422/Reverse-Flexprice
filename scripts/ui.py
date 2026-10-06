#!/usr/bin/env python3
"""Seed a demo tenant and open the dashboard in the browser.

The demo data is described in app/demo_seed.py. It is written to a throwaway
SQLite file, the clock is frozen at 2026-05-01 00:30 UTC, and /app opens.
Ctrl+C to stop.

Run:  ./scripts/ui          (Windows: scripts\\ui)
"""
import os
import socket
import sys
import tempfile
import threading
import time
import warnings
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_TMP = tempfile.mkdtemp(prefix="billing_ui_")
os.environ["DATABASE_URL"] = f"sqlite:///{Path(_TMP) / 'ui.db'}"
os.environ["BILLING_PERIOD_MINUTES"] = "0"

warnings.filterwarnings("ignore")  # starlette testclient deprecation noise

import uvicorn  # noqa: E402

from app.demo_seed import NOW, ensure_seeded  # noqa: E402
from app.main import app  # noqa: E402


def _free_port(preferred: int = 8000) -> int:
    for port in (preferred, 0):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return s.getsockname()[1]
            except OSError:
                continue
    raise SystemExit("no free port")


def main() -> None:
    print("  Seeding demo tenant (4 customers, 2 x 7-day usage histories)...",
          flush=True)
    t0 = time.perf_counter()
    ensure_seeded(app)
    print(f"  Seeded in {time.perf_counter() - t0:.1f}s", flush=True)

    port = _free_port()
    url = f"http://127.0.0.1:{port}/app"
    print(f"\n  Dashboard:  {url}"
          f"\n  Server time is frozen at {NOW}.  Ctrl+C to stop.\n", flush=True)
    if "--no-browser" not in sys.argv:
        threading.Timer(1.2, webbrowser.open, args=(url,)).start()
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
