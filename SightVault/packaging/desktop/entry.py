"""PyInstaller entry point for the SightVault desktop app."""
import multiprocessing
import sys

if __name__ == '__main__':
    # Required before anything can start a multiprocessing child in a frozen app.
    multiprocessing.freeze_support()
    from sightvault.desktop.main import main
    sys.exit(main())
