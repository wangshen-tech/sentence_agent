"""PyInstaller entry point for the .app bundle."""

import sys

from sentence_agent.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
