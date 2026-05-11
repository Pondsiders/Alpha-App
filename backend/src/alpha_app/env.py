"""Captured environment — secrets read at startup, removed from os.environ.

Reading credentials at import time and popping them from os.environ in
the same breath prevents child processes from inheriting them. The
Claude SDK subprocess (and anything it spawns) sees a sanitized
environment; the captured values live here for the parent process to
use.

Anything that needs a captured secret imports the constant from this
module rather than reading os.environ. There is no fallback to
os.environ after this module loads — the value either was captured at
import time or it wasn't.
"""

import os

DATABASE_URL: str | None = os.environ.pop("DATABASE_URL", None)
"""The Postgres DSN, captured once at import time. None if not set."""
