#!/usr/bin/env python3
"""Retired public single-question apply entrypoint."""

import sys


def main() -> int:
    sys.stderr.write("公开单题同步入口已退役；请使用显式日期 Sol curation。\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
