"""Headless TUI smoke test: the app mounts, and a plain-text line (an Autobot
prompt, which lands in v2) gets the explanatory notice instead of a fake reply."""

import asyncio

from sagex import config
from sagex.api import store
from sagex.app import SagexApp


def test_plain_text_gets_the_v2_notice(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(store, "get_key", lambda: None)    # local env: no server calls

    async def scenario():
        app = SagexApp()
        async with app.run_test() as pilot:
            await pilot.press(*"hello", "enter")
            await pilot.pause()
            notices = list(app.query("ChatMessage.notice"))
            assert len(notices) == 1
            assert "sagex v2" in notices[0]._body
            assert not list(app.query("ChatMessage.autobot"))   # no pretend reply

    asyncio.run(scenario())
