"""python -m app [--region L,T,W,H] [--lang ja|en] [--device cuda|cpu] [--no-hotkeys] [--no-guard]
[--exit-after SECONDS] [--debug-dump DIR] [--log-level INFO]"""
import sys

from app.main import main

if __name__ == "__main__":
    sys.exit(main())
