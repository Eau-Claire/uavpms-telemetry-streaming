"""python -m publisher [--env FILE]"""
from __future__ import annotations

import argparse
import logging
import sys

from .app import run
from .config import ConfigError, load_settings


def main() -> int:
    p = argparse.ArgumentParser(prog="publisher", description="Mission Planner MAVLink -> MQTT telemetry publisher")
    p.add_argument("--env", default=".env", help="env file to load (default .env; real env vars take precedence)")
    a = p.parse_args()
    try:
        s = load_settings(a.env)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    logging.basicConfig(level=getattr(logging, s.log_level, logging.INFO),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("publisher").info("settings %s", s.redacted())
    run(s)
    return 0


if __name__ == "__main__":
    sys.exit(main())
