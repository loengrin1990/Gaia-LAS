#!/usr/bin/env python3
from __future__ import annotations

import sys


if __name__ == "__main__":
    try:
        from gaia.server import main
    except Exception as exc:
        print(f"Gaia startup error: {exc}", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(main())
