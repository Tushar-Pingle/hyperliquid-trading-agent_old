"""Phase 1 (Truth Layer): pure helpers for interpreting Hyperliquid userFills.

These are deliberately dependency-free and side-effect-free so they can be
unit-tested exhaustively against synthetic fill fixtures — the previous code
lost track of every exchange-side exit (35 reconcile_close pnl=null events,
including the bot's biggest win) because of two bugs these helpers fix:

  1. get_recent_fills sliced ``fills[-limit:]`` on a NEWEST-FIRST list, so once
     lifetime fills exceeded ``limit`` it returned the OLDEST fills — the exit
     fills for a just-closed position were never seen. ``newest_fills`` is
     order-independent (sorts by time) so it cannot regress on either ordering.
  2. Direction was read from ``isBuy``, a key raw userFills do not carry (they
     use ``side`` "B"/"A" and ``dir`` "Open Long"/"Close Long"/...), so the
     entry/exit filter was a silent no-op. ``fill_is_buy`` reads all three.

Hyperliquid fill schema (fields used here): coin, oid, time (ms), px, sz,
side ("B"|"A"), dir ("Open Long"|"Close Long"|"Open Short"|"Close Short"|...),
closedPnl (realized gross PnL on the closed portion; 0 on opens), fee.
"""


def fill_time_ms(f: dict) -> int:
    """Fill timestamp in ms (0 if absent/unparseable)."""
    try:
        return int(f.get("time") or f.get("timestamp") or 0)
    except (TypeError, ValueError):
        return 0


def fill_is_buy(f: dict):
    """True/False for buy/sell, or None if genuinely undeterminable.

    Prefers an explicit ``isBuy`` bool, then ``side`` (B/A), then the
    human-readable ``dir`` string.
    """
    if isinstance(f.get("isBuy"), bool):
        return f["isBuy"]
    s = str(f.get("side", "")).strip().upper()
    if s in ("B", "BUY", "BID"):
        return True
    if s in ("A", "S", "SELL", "ASK"):
        return False
    d = str(f.get("dir", "")).strip().lower()
    # A buy trade opens a long or closes a short; a sell opens a short / closes a long.
    if "open long" in d or "close short" in d or d == "buy":
        return True
    if "open short" in d or "close long" in d or d == "sell":
        return False
    return None


def fill_coin_matches(f: dict, asset: str) -> bool:
    """Whether a fill belongs to ``asset``, tolerating the HIP-3 ``dex:`` prefix.

    active_trades may store ``xyz:CL`` while a fill reports ``CL`` (or vice
    versa) — match on the bare symbol either way.
    """
    fc = f.get("coin") or f.get("asset") or ""
    if not fc:
        return False
    if fc == asset:
        return True
    a_bare = asset.split(":", 1)[1] if ":" in asset else asset
    f_bare = fc.split(":", 1)[1] if ":" in fc else fc
    return a_bare == f_bare


def newest_fills(fills, limit: int):
    """Return the ``limit`` MOST-RECENT fills, independent of input ordering."""
    if not isinstance(fills, list):
        return []
    return sorted(fills, key=fill_time_ms, reverse=True)[: max(0, limit)]


def is_closing_fill(f: dict) -> bool:
    """Whether a fill reduced/closed a position (has realized PnL or a Close dir)."""
    try:
        if float(f.get("closedPnl") or 0) != 0:
            return True
    except (TypeError, ValueError):
        pass
    return "close" in str(f.get("dir", "")).lower()


def realized_from_fills(fills):
    """Aggregate one position's fills into realized economics.

    Args:
        fills: fills belonging to a single position's lifetime (opens + closes).

    Returns dict:
        net_pnl   — gross closedPnl minus ALL fees in the window (None if empty)
        gross_pnl — sum of closedPnl (exchange realized, pre-fee)
        fees      — sum of fee across the window (entry + exit)
        exit_px   — price of the last closing fill (or last fill if none flagged)
        n_fills   — number of fills aggregated
    """
    if not fills:
        return {"net_pnl": None, "gross_pnl": None, "fees": 0.0, "exit_px": None, "n_fills": 0}
    ordered = sorted(fills, key=fill_time_ms)

    def _num(f, key):
        try:
            return float(f.get(key) or 0)
        except (TypeError, ValueError):
            return 0.0

    gross = sum(_num(f, "closedPnl") for f in ordered)
    fees = sum(_num(f, "fee") for f in ordered)
    closing = [f for f in ordered if is_closing_fill(f)] or ordered
    exit_px = None
    try:
        exit_px = float(closing[-1].get("px") or 0) or None
    except (TypeError, ValueError):
        exit_px = None
    return {
        "net_pnl": gross - fees,
        "gross_pnl": gross,
        "fees": fees,
        "exit_px": exit_px,
        "n_fills": len(ordered),
    }


def classify_fill(f: dict, tr: dict) -> str:
    """Classify a fill against a tracked trade record.

    Returns one of: 'entry', 'tp', 'sl', 'close', 'liquidation', 'unknown'.
    oid matches (tp/sl/entry) win over the ``dir`` heuristic.
    """
    oid = f.get("oid")
    if tr and oid is not None:
        s = str(oid)
        if s in (str(tr.get("tp_oid")), str(tr.get("tp1_oid")), str(tr.get("tp2_oid"))):
            return "tp"
        if s == str(tr.get("sl_oid")):
            return "sl"
        if s == str(tr.get("entry_oid")):
            return "entry"
    d = str(f.get("dir", "")).lower()
    if "liquidat" in d:
        return "liquidation"
    if "open" in d:
        return "entry"
    if "close" in d:
        return "close"
    return "unknown"
