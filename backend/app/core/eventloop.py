"""The event loop uvicorn runs the backend on (R32, Phase 2C).

uvicorn's default asyncio loop on Windows is `ProactorEventLoop`. In CPython 3.12 its accept
loop (`proactor_events.py`, `_start_serving`) **closes the listening socket** when an IOCP
accept fails, which happens with WinError 64 whenever a client resets a connection before
the server has accepted it. The process keeps running but refuses every later connection,
and asyncio logs "Accept failed on a socket" with a traceback. Reproduced in Phase 2C with
200 connect-and-reset clients (`reports/phase-2c.md` §2.3).

The selector loop accepts through `select()` and has no such path, so the backend always
uses it. Limit: `select()` handles at most 512 sockets on Windows, ample for the single-user,
loopback-only MVP. On other platforms the selector loop is asyncio's default anyway.
"""

import asyncio

# uvicorn `loop` setting: an import string naming a callable that returns a new event loop.
UVICORN_LOOP = "app.core.eventloop:new_event_loop"


def new_event_loop() -> asyncio.AbstractEventLoop:
    return asyncio.SelectorEventLoop()
