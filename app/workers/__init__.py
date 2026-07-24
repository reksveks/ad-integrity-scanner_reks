"""Shared utilities for worker processes."""
from __future__ import annotations

import asyncio
import signal
import sys


def setup_signals(stop_event: asyncio.Event) -> None:
    """Register shutdown signals in a cross-platform way.

    On Unix, asyncio's add_signal_handler is used (async-safe, loop-aware).
    On Windows, add_signal_handler is not available and SIGTERM does not exist,
    so we fall back to signal.signal with call_soon_threadsafe.
    """
    loop = asyncio.get_running_loop()
    if sys.platform == "win32":
        # Only SIGINT (Ctrl-C) is reliably catchable on Windows.
        signal.signal(
            signal.SIGINT,
            lambda s, f: loop.call_soon_threadsafe(stop_event.set),
        )
    else:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop_event.set)
