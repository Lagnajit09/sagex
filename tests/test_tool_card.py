"""ToolCard folding: what's visible while running, after success, after failure."""

from sagex.widgets.tool_card import FOLDED_LINES, LIVE_LINES, ToolCard

LINES = [f"line {i}" for i in range(40)]


def _card(lines=LINES, **kwargs):
    card = ToolCard("sagex_read", "sagex list run", **kwargs)
    card.set_lines(lines)
    return card


def test_running_shows_the_live_end():
    shown, hidden, hidden_first = _card()._visible()
    assert shown == LINES[-LIVE_LINES:] and hidden == 40 - LIVE_LINES and hidden_first


def test_success_folds_to_the_start_and_expands():
    card = _card()
    card.finish(0, 0.4, sage_saw="Sage saw all of it")
    assert card._visible() == (LINES[:FOLDED_LINES], 40 - FOLDED_LINES, False)
    card.on_click()
    assert card._visible()[0] == LINES
    card.on_click()
    assert card._visible()[0] == LINES[:FOLDED_LINES]


def test_failure_folds_to_the_end_where_the_error_is():
    card = _card()
    card.finish(1, 0.4, sage_saw="Sage saw all of it")
    assert card._visible() == (LINES[-FOLDED_LINES:], 40 - FOLDED_LINES, True)
    assert card._footer.startswith("✗ exit 1 · 0.4s")


def test_help_card_starts_folded_and_has_no_duration():
    card = _card(folded_lines=0)
    card.finish(0, None, sage_saw="Sage read it")
    assert card._visible()[0] == [] and card._footer == "✓ · Sage read it"


def test_refused_card_keeps_the_reason():
    card = ToolCard("sagex_read", "sagex delete vault prod")
    card.refuse("changes things")
    assert card.state == "refused" and card._note == "changes things" and card._visible()[0] == []
