"""Phase 0 (0.6): non-blocking operator alert channel.

Every failure class in the audit (69h API outage, tpsl_failed, naked positions,
breaker trips) ran for hours-to-days because nothing paged a human. This module
provides a single ``notify(msg, level)`` that ALWAYS logs and, if
``ALERT_WEBHOOK_URL`` is set, fires the message to a webhook in a daemon thread
so it never blocks the trading loop and never raises into it.

Supported webhook targets:
  * ntfy.sh style — if the URL contains "ntfy", the message is POSTed as the
    plain-text body with a Title/Priority header.
  * generic — otherwise the message is POSTed as JSON ``{"text", "level"}``
    (works for most webhook receivers; for Telegram use a tiny relay or an
    ntfy topic — a raw Telegram sendMessage URL needs query params, which this
    keeps out of scope on purpose).

The webhook URL is read from the environment on every call so a ``.env`` reload
or a late-set variable is picked up without a restart.
"""

import logging
import os
import threading

try:  # requests is a hard dependency of the project, but stay import-safe
    import requests
except Exception:  # pragma: no cover - defensive
    requests = None

_LEVELS = {
    "info": logging.INFO,
    "warn": logging.WARNING,
    "warning": logging.WARNING,
    "error": logging.ERROR,
    "critical": logging.CRITICAL,
}

# ntfy priority mapping (1=min .. 5=max)
_NTFY_PRIORITY = {
    "info": "3",
    "warn": "4",
    "warning": "4",
    "error": "5",
    "critical": "5",
}


def _post(url: str, msg: str, level: str) -> None:
    """Best-effort webhook POST — swallows every error."""
    if requests is None:
        return
    try:
        if "ntfy" in url:
            requests.post(
                url,
                data=msg.encode("utf-8"),
                headers={
                    "Title": f"hl-bot [{level}]",
                    "Priority": _NTFY_PRIORITY.get(level, "3"),
                },
                timeout=5,
            )
        else:
            requests.post(url, json={"text": msg, "level": level}, timeout=5)
    except Exception as exc:  # pragma: no cover - network best-effort
        logging.warning("notify: webhook POST failed: %s", exc)


def notify(msg: str, level: str = "info") -> None:
    """Log an operator alert and (if configured) fire it to a webhook.

    Never blocks: the webhook POST runs in a daemon thread. Never raises.
    """
    logging.log(_LEVELS.get(level, logging.INFO), "ALERT[%s]: %s", level, msg)
    url = os.getenv("ALERT_WEBHOOK_URL")
    if not url:
        return
    try:
        threading.Thread(target=_post, args=(url, msg, level), daemon=True).start()
    except Exception as exc:  # pragma: no cover - defensive
        logging.warning("notify: failed to spawn alert thread: %s", exc)
