"""SageCode usage: the sidebar block's state and the /usage report (sagex/usage.py)."""

from sagex import usage


def test_compact_numbers():
    assert [usage.compact(n) for n in (0, 999, 1000, 48210, 1_250_000)] == ["0", "999", "1k", "48.2k", "1.2M"]


def test_today_from_usage_and_replies():
    today = usage.Today.from_usage({"today": {"requests": 2, "total_tokens": 900},
                                    "admin_quota": {"used": 5, "limit": 30, "remaining": 25}})
    assert "2 requests · 900 tokens" in str(today.sidebar()) and "25 of 30 left" in str(today.sidebar())
    today.apply_done({"cancelled": True})                            # stopped: no usage
    today.apply_done({"usage": {"total_tokens": 100}, "quota": {"used": 6, "limit": 30, "remaining": 24}})
    assert (today.requests, today.tokens, today.quota["remaining"]) == (3, 1000, 24)
    today.apply_done({"usage": {"total_tokens": 10}, "byo": True})   # switched to their own key
    assert "your own key · no daily cap" in str(today.sidebar())


CLAUDE = {"id": "m1", "name": "My Claude", "provider": "anthropic", "model_name": "claude-sonnet-4-5"}


def test_sidebar_names_the_model_in_use():
    free = usage.Today.from_usage({"model": None, "admin_quota": {"used": 1, "limit": 30, "remaining": 29}})
    assert "Model Autosage free models" in str(free.sidebar()) and "29 of 30 left" in str(free.sidebar())
    mine = usage.Today.from_usage({"model": CLAUDE, "own_key": True})
    assert "Model My Claude · no daily cap" in str(mine.sidebar())
    assert "free requests" not in str(mine.sidebar()).lower()
    long = usage.Today.from_usage({"model": {**CLAUDE, "name": "A very long model name indeed, really"}})
    assert "A very long model nam…" in str(long.sidebar())


def test_sidebar_says_when_the_selected_model_failed():
    today = usage.Today.from_usage({"model": CLAUDE, "own_key": True})
    # The server fell back to the free models (broken key): the reply ticked the allowance.
    today.apply_done({"usage": {"total_tokens": 5}, "byo": False,
                      "quota": {"used": 2, "limit": 30, "remaining": 28}})
    assert "My Claude failed" in str(today.sidebar()) and "free models" in str(today.sidebar())
    today.apply_done({"usage": {"total_tokens": 5}, "byo": True})    # working again
    assert "Model My Claude · no daily cap" in str(today.sidebar())


def test_selecting_a_model_updates_the_sidebar():
    today = usage.Today.from_usage({"admin_quota": {"used": 1, "limit": 30, "remaining": 29}})
    today.select(CLAUDE)
    assert "Model My Claude" in str(today.sidebar())
    today.select(None)
    assert "Model Autosage free models" in str(today.sidebar())


def test_allowance_warnings():
    low = usage.Today(quota={"used": 27, "limit": 30, "remaining": 3})
    assert low.sidebar().spans[-1].style == "yellow"
    low.allowance_used_up()
    assert "used up" in str(low.sidebar()) and low.sidebar().spans[-1].style == "red"
    assert "/usage" in str(usage.Today().sidebar())                  # no cap known


def test_report():
    text = usage.report({
        "today": {"requests": 1, "total_tokens": 1500, "byo_tokens": 0, "admin_tokens": 1500},
        "last_7d": {"requests": 3, "total_tokens": 4500, "model_usage": [
            {"provider": "groq", "model": "llama", "count": 3}]},
        "all_time": {"requests": 3, "total_tokens": 4500},
        "admin_quota": {"used": 1, "limit": 30, "remaining": 29},
        "daily": [{"requests": n} for n in (0, 0, 1, 0, 0, 1, 1)],
        "devices": [{"device": "laptop", "requests": 3}],
    })
    assert "Today" in text and "1,500" in text and "29 of 30 left" in text
    assert "▁▁█▁▁██" in text and "groq/llama (3)" in text and "laptop (3)" in text
    assert "/agent/dashboard?tab=sagecode" in text
    mine = usage.report({"model": CLAUDE, "own_key": True})
    assert "My Claude · anthropic/claude-sonnet-4-5" in mine and "no daily cap" in mine
