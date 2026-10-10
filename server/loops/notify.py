"""Operator notifications for the memory maintenance jobs.

memory-alerts (every 30 minutes) and memory-healthcheck (daily) send their
findings to Telegram when TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are set.

A small state record in Neo4j (label DipinkAlertState, outside the Graphiti
labels) stops repeated messages:

- A new set of firing alerts sends a message at once.
- A set that does not change sends a reminder after NOTIFY_REPEAT_HOURS.
- When all alerts clear, one "resolved" message goes out.

Each alert has an owner. "lead" means a project lead agent can fix it; the
dip.ink lead files a ticket for it in its daily review (the job prints an
ALERTS_JSON line for that review). "operator" means only the operator can fix
it, for example an expired provider credential or an exhausted quota.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.request
from dataclasses import asdict, dataclass

log = logging.getLogger("notify")

TELEGRAM_API = os.environ.get("TELEGRAM_API_BASE", "https://api.telegram.org").rstrip("/")
REPEAT_HOURS = float(os.environ.get("NOTIFY_REPEAT_HOURS", "12"))
INSTANCE = os.environ.get("NOTIFY_INSTANCE", "dip.ink memory")
USER_AGENT = "dip.ink-memory-notify/1"
MESSAGE_MAX = 3800  # Telegram rejects text over 4096 characters.

OWNER_LEAD = "lead"
OWNER_OPERATOR = "operator"

# Text that shows a problem only the operator can fix.
_OPERATOR_RE = re.compile(
    r"\b(401|403|unauthori[sz]ed|forbidden|invalid[ _-]?api[ _-]?key|insufficient_quota|"
    r"quota|billing|credential|token (?:expired|revoked))\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Alert:
    key: str
    message: str
    owner: str = OWNER_LEAD


def classify_owner(text: str) -> str:
    return OWNER_OPERATOR if _OPERATOR_RE.search(text or "") else OWNER_LEAD


def alert_key(text: str) -> str:
    """A stable key from free text: the part before the first colon."""
    head = (text or "").split(":", 1)[0].strip().lower()
    return re.sub(r"[^a-z0-9]+", "-", head).strip("-")[:60] or "unknown"


def telegram_configured() -> bool:
    return bool(os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"))


def send_telegram(text: str) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not (token and chat):
        return False
    if len(text) > MESSAGE_MAX:
        text = text[: MESSAGE_MAX - 20] + "\n… (truncated)"
    payload = json.dumps({"chat_id": chat, "text": text, "disable_web_page_preview": True}).encode()
    request = urllib.request.Request(
        f"{TELEGRAM_API}/bot{token}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            ok = bool(json.load(response).get("ok"))
    except Exception as error:  # noqa: BLE001
        # The URL contains the token: report only the error class.
        print(f"telegram send failed: {type(error).__name__}")
        return False
    print(f"telegram send ok: {ok}")
    return ok


class Neo4jStateStore:
    """Keeps one (fingerprint, last_sent) record per job name in Neo4j."""

    def __init__(self) -> None:
        from neo4j import GraphDatabase

        self._driver = GraphDatabase.driver(
            os.environ.get("NEO4J_URI", "bolt://neo4j:7687"),
            auth=(os.environ.get("NEO4J_USER", "neo4j"), os.environ.get("NEO4J_PASSWORD", "")),
            connection_timeout=10,
        )

    def get(self, name: str) -> tuple[str, float]:
        records, _, _ = self._driver.execute_query(
            "MATCH (s:DipinkAlertState {name: $name}) RETURN s.fingerprint AS fp, s.last_sent AS ts",
            name=name,
        )
        if not records:
            return "", 0.0
        return records[0]["fp"] or "", float(records[0]["ts"] or 0.0)

    def put(self, name: str, fingerprint: str, last_sent: float) -> None:
        self._driver.execute_query(
            "MERGE (s:DipinkAlertState {name: $name}) SET s.fingerprint = $fp, s.last_sent = $ts",
            name=name, fp=fingerprint, ts=last_sent,
        )

    def close(self) -> None:
        self._driver.close()


def fingerprint(alerts: list[Alert]) -> str:
    return ",".join(sorted({a.key for a in alerts}))


def decide(previous: str, last_sent: float, current: str, now: float, repeat_hours: float) -> str | None:
    """Return "fire", "repeat", "resolved", or None (send nothing)."""
    if current and current != previous:
        return "fire"
    if current and now - last_sent >= repeat_hours * 3600:
        return "repeat"
    if not current and previous:
        return "resolved"
    return None


def format_message(title: str, alerts: list[Alert], action: str, previous: str = "") -> str:
    if action == "resolved":
        cleared = previous.replace(",", ", ") or "all"
        return f"✅ {INSTANCE}: {title} resolved ({cleared})"
    head = "🚨" if action == "fire" else "🔁"
    word = "firing" if action == "fire" else f"still firing (reminder every {REPEAT_HOURS:g}h)"
    lines = [f"{head} {INSTANCE}: {len(alerts)} {title} alert(s) {word}", ""]
    for alert in alerts:
        who = "Diego" if alert.owner == OWNER_OPERATOR else "lead"
        lines.append(f"• [{who}] {alert.message}")
    if any(a.owner == OWNER_LEAD for a in alerts):
        lines += ["", "[lead] alerts go to a dip.ink ticket in the daily lead review."]
    if any(a.owner == OWNER_OPERATOR for a in alerts):
        lines += ["[Diego] alerts need your action."]
    return "\n".join(lines)


def print_alerts_json(job: str, alerts: list[Alert], warnings: list[str]) -> None:
    """One machine-readable line for the daily lead review."""
    print("ALERTS_JSON " + json.dumps({
        "job": job,
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "alerts": [asdict(a) for a in alerts],
        "warnings": warnings,
    }, ensure_ascii=False))


def notify(
    name: str,
    title: str,
    alerts: list[Alert],
    *,
    store=None,
    now: float | None = None,
    throttle_without_state: bool = True,
) -> str | None:
    """Send the Telegram message that the state change needs.

    Returns the action that was sent, or None. Returns "send-failed" when
    Telegram did not accept the message.

    When the state store is not available, the job cannot see the last
    message. A frequent job then sends firing alerts only in the first half
    hour of every fourth UTC hour (throttle_without_state), and sends no
    resolved message.
    """
    if not telegram_configured():
        return None
    now = time.time() if now is None else now
    current = fingerprint(alerts)
    owned_store = False
    if store is None:
        try:
            store = Neo4jStateStore()
            owned_store = True
        except Exception as error:  # noqa: BLE001
            print(f"alert state store unavailable: {type(error).__name__}")
    try:
        previous, last_sent = "", 0.0
        if store is not None:
            try:
                previous, last_sent = store.get(name)
            except Exception as error:  # noqa: BLE001
                print(f"alert state read failed: {type(error).__name__}")
                store = None
        action = decide(previous, last_sent, current, now, REPEAT_HOURS)
        if action and store is None and throttle_without_state:
            clock = time.gmtime(now)
            if not (clock.tm_hour % 4 == 0 and clock.tm_min < 30):
                print(f"notify: no state store; {name} message held until the next 4-hour slot")
                return None
        if action is None:
            print(f"notify: no change for {name} (firing={current or 'none'})")
            return None
        if not send_telegram(format_message(title, alerts, action, previous)):
            return "send-failed"
        if store is not None:
            try:
                store.put(name, current, now)
            except Exception as error:  # noqa: BLE001
                print(f"alert state write failed: {type(error).__name__}")
        return action
    finally:
        if owned_store and store is not None:
            store.close()
