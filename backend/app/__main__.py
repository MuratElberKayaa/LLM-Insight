"""Local dev server: `python -m app` (single worker, loopback bind from settings)."""

import uvicorn

from app.core.config import get_settings
from app.core.eventloop import UVICORN_LOOP


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "app.main:create_app",
        factory=True,
        host=settings.bind_host,
        port=settings.port,
        workers=1,  # in-process rate limiting assumes one process (CR-08)
        loop=UVICORN_LOOP,  # selector loop: the Windows Proactor one can stop accepting (R32)
    )


if __name__ == "__main__":
    main()
