"""Retired entry point for the pre-workspace demo dataset."""

import sys


def main() -> int:
    print('DEMO_COMMAND_DISABLED: use the isolated test fixtures in docs/TESTING.md.', file=sys.stderr)
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
