"""SageCode usage: the small block at the bottom of the sidebar and `/usage`.

The server totals saved turns (Autosage `GET /api/ai/sage/usage/`: today, 7
days, all time, per day, models, devices) and adds the free daily allowance,
which is shared with Autobot on the web. The sidebar fetches that once, then
keeps itself current from each reply's `done` frame (its tokens and the
allowance after it), so it never costs a request per reply. F5 and `/usage`
fetch again, which also picks up what was used on the web meanwhile.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from rich.text import Text

from sagex import config, models

_SPARKS = "▁▂▃▄▅▆▇█"


def _short(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def compact(n: int) -> str:
    """48210 → '48.2k', 1_250_000 → '1.3M'."""
    n = int(n or 0)
    for size, suffix in ((1_000_000, "M"), (1_000, "k")):
        if n >= size:
            value = f"{n / size:.1f}".rstrip("0").rstrip(".")
            return f"{value}{suffix}"
    return str(n)


@dataclass
class Today:
    """What the sidebar shows: today's requests and tokens, and the allowance."""

    requests: int = 0
    tokens: int = 0
    own_key: bool = False
    quota: dict | None = None            # {used, limit, remaining}; None: no cap known
    model: dict | None = None            # the selected personal model {id, name, provider, model_name}
    model_failed: bool = False           # it was selected, but the last reply came from the free models
    extra: dict = field(default_factory=dict)

    @classmethod
    def from_usage(cls, data: dict) -> "Today":
        today = data.get("today") or {}
        quota = data.get("admin_quota")
        model = data.get("model") if isinstance(data.get("model"), dict) else None
        return cls(requests=int(today.get("requests") or 0), tokens=int(today.get("total_tokens") or 0),
                   own_key=bool(data.get("own_key")) or model is not None, model=model,
                   quota=quota if isinstance(quota, dict) and quota.get("limit") else None)

    def select(self, model: dict | None) -> None:
        """The user picked a model (/model); shown until the next fetch."""
        self.model, self.own_key, self.model_failed = model, model is not None, False

    def apply_done(self, frame: dict) -> None:
        """Count a finished reply (cancelled ones carry no usage)."""
        if frame.get("cancelled"):
            return
        self.requests += 1
        self.tokens += int((frame.get("usage") or {}).get("total_tokens") or 0)
        if frame.get("byo"):
            self.own_key, self.model_failed = True, False
        elif isinstance(frame.get("quota"), dict):
            # A selected model whose key or settings don't work: Sage quietly
            # used the free models instead — say so rather than show its name.
            self.model_failed = self.model is not None
            self.own_key = False
            self.quota = frame["quota"]

    def allowance_used_up(self) -> None:
        if self.quota:
            self.quota = {**self.quota, "used": self.quota["limit"], "remaining": 0}

    def sidebar(self) -> Text:
        text = Text()
        text.append("Sage Today ", style="bold")
        text.append(f"{self.requests} request{'' if self.requests == 1 else 's'} · "
                    f"{compact(self.tokens)} tokens\n", style="bright_black")
        name = _short(str((self.model or {}).get("name") or "your own key"), 22)
        if self.model_failed:
            text.append(f"Model {name} failed\n", style="yellow")
            text.append("Using free models · /model to check", style="yellow")
            return text
        text.append("Model ", style="bold")
        if self.own_key:
            text.append(name)
            text.append(" · no daily cap", style="bright_black")
            return text
        text.append(f"{models.FREE_LABEL}\n")
        if self.quota:
            left, limit = int(self.quota.get("remaining") or 0), int(self.quota["limit"])
            if left <= 0:
                text.append("Free requests used up · resets 00:00 UTC", style="red")
            else:
                style = "yellow" if left <= max(1, limit // 5) else "bright_black"
                text.append(f"Free requests: {left} of {limit} left", style=style)
        else:
            text.append("/usage for details", style="bright_black")
        return text


def _row(label: str, bucket: dict) -> str:
    requests = int(bucket.get("requests") or 0)
    tokens = int(bucket.get("total_tokens") or 0)
    own, free = int(bucket.get("byo_tokens") or 0), int(bucket.get("admin_tokens") or 0)
    return (f"  {label:<12}{requests:>9}{tokens:>12,}   {compact(own)} own key · {compact(free)} free")


def report(data: dict) -> str:
    """The `/usage` text."""
    lines = ["SageCode usage", f"  {'':<12}{'Requests':>9}{'Tokens':>12}   Tokens by key"]
    for label, key in (("Today", "today"), ("Last 7 days", "last_7d"), ("All time", "all_time")):
        lines.append(_row(label, data.get(key) or {}))

    quota = data.get("admin_quota") or {}
    model = data.get("model") if isinstance(data.get("model"), dict) else None
    if model:
        lines.append(f"Sage answers with your model {models.label(model)}, so there's no daily cap.")
    elif data.get("own_key"):
        lines.append("Sage uses your own LLM key, so there's no daily cap.")
    elif quota.get("limit"):
        lines.append(f"Free requests today: {quota.get('remaining', 0)} of {quota['limit']} left "
                     "(shared with Autobot on the web; resets 00:00 UTC).")

    daily = data.get("daily") or []
    if daily:
        peak = max(int(d.get("requests") or 0) for d in daily)
        bars = "".join(_SPARKS[min(len(_SPARKS) - 1, (int(d.get("requests") or 0) * (len(_SPARKS) - 1))
                                   // peak)] if peak else _SPARKS[0] for d in daily)
        lines.append(f"Requests per day, last 7: {bars}  ({sum(int(d.get('requests') or 0) for d in daily)} "
                     f"in all, today last)")

    used = (data.get("last_7d") or {}).get("model_usage") or []
    if used:
        lines.append("Models, last 7 days: " + ", ".join(
            f"{m.get('provider')}/{m.get('model')} ({m.get('count')})" for m in used[:4]))
    devices = data.get("devices") or []
    if devices:
        lines.append("Devices: " + ", ".join(f"{d.get('device')} ({d.get('requests')})" for d in devices[:4]))
    lines.append(f"Full dashboard: {config.WEB_URL}/agent/dashboard?tab=sagecode")
    return "\n".join(lines)
