"""PyInstaller entry point; all dispatch belongs to the installed application."""
from pbdesk.packaged import main

if __name__ == '__main__':
    raise SystemExit(main())
