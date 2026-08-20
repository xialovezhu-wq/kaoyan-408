#!/usr/bin/env python3
"""Portable next-morning preparation marker; no learner-turn writes."""

import sys


def main() -> int:
    sys.stdout.write('{"status":"deferred_to_explicit_date_sol","formal_write_count":0}\n')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
