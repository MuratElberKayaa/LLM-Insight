"""Local dev server: `python -m app` (single worker, loopback bind from settings)."""

import uvicorn

from app.core.config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "app.main:create_app",
        factory=True,
        host=settings.bind_host,
        port=settings.port,
        workers=1,  # in-process rate limiting assumes one process (CR-08)
    )


if __name__ == "__main__":
    main()
