"""R32: the backend runs on the selector event loop (app/core/eventloop.py)."""

import asyncio

import uvicorn
from uvicorn.config import Config

import app.__main__ as dev_server
from app.core import eventloop
from tests.helpers import make_settings


def test_the_factory_builds_a_selector_loop():
    loop = eventloop.new_event_loop()
    try:
        assert isinstance(loop, asyncio.SelectorEventLoop)
        assert not isinstance(loop, getattr(asyncio, "ProactorEventLoop", ()))
    finally:
        loop.close()


def test_uvicorn_resolves_the_setting_to_that_factory():
    config = Config("app.main:create_app", factory=True, loop=eventloop.UVICORN_LOOP)
    assert config.get_loop_factory() is eventloop.new_event_loop


def test_python_m_app_passes_the_loop_setting(monkeypatch):
    seen = {}
    monkeypatch.setattr(dev_server, "get_settings", lambda: make_settings())
    monkeypatch.setattr(uvicorn, "run", lambda *a, **kw: seen.update(kw))
    dev_server.main()
    assert seen["loop"] == eventloop.UVICORN_LOOP
    assert seen["workers"] == 1 and seen["host"] == "127.0.0.1"
