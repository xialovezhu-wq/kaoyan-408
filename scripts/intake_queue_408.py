#!/usr/bin/env python3
"""Retired asynchronous queue submission entrypoint."""

import sys


def main() -> int:
    sys.stderr.write("新异步提交已于 2026-07-12 退役；不得创建新 queue。\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
