"""Event loop factory handed to uvicorn (``--loop pi_jira_agent.eventloop:loop_factory``).

psycopg's async driver cannot run on Windows' default ProactorEventLoop, and uvicorn
0.49 forces that loop whenever it runs in-process on Windows. Supplying our own
factory sidesteps both. Subprocesses (the Pi runner, git) use subprocess.run in a
thread, so the selector loop's lack of asyncio subprocess support does not matter.
"""

from __future__ import annotations

import asyncio
import sys


def loop_factory() -> asyncio.AbstractEventLoop:
    if sys.platform == "win32":
        return asyncio.SelectorEventLoop()
    return asyncio.new_event_loop()


LOOP_SPEC = "pi_jira_agent.eventloop:loop_factory"
