"""Backward-compatible entry point for explicit portable package builds."""
from build_portable_package import main


if __name__ == '__main__':
    raise SystemExit(main())
