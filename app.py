#!/usr/bin/env python3
from __future__ import annotations

import sys
import argparse


if __name__ == "__main__":
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument(
        "--no-window",
        action="store_true",
        help="Запустить локальный сервер без системного окна.",
    )
    arguments = parser.parse_args()
    try:
        from gaia.server import main
    except Exception as exc:
        print(f"Gaia startup error: {exc}", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(main(open_window=not arguments.no_window))
