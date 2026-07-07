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
    versa) — match on the bare symbol when exactly one side is prefixed. But if
    BOTH sides are dex-prefixed they must match EXACTLY: two HIP-3 dexes exposing
    the same bare symbol (``dexA:CL`` vs ``dexB:CL``) are different markets and
    must not cross-attribute fills.
    """
    fc = f.get("coin") or f.get("asset") or ""
    if not fc:
        return False
    if fc == asset:
        return True
    a_pref = ":" in asset
    f_pref = ":" in fc
    if a_pref and f_pref:
        return False  # both prefixed but not identical → different markets
    a_bare = asset.split(":", 1)[1] if a_pref else asset
    f_bare = fc.split(":", 1)[1] if f_pref else fc
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
        has_close — whether the window contains at least one CLOSING fill
        net_pnl   — gross closedPnl minus ALL fees (None if no closing fill)
        gross_pnl — sum of closedPnl (exchange realized, pre-fee)
        fees      — sum of fee across the window (entry + exit)
        exit_px   — price of the last closing fill (None if no closing fill)
        n_fills   — number of fills aggregated

    CRITICAL (Phase 1 review): if the window has NO closing fill — e.g. the exit
    fill is lagging the exchange's clearinghouse update — we return has_close
    False / net_pnl None / exit_px None so the caller ALERTS rather than recording
    a synthetic breakeven close at the entry price (a plausible-looking lie is
    worse than a null).
    """
    if not fills:
        return {"net_pnl": None, "gross_pnl": None, "fees": 0.0, "exit_px": None,
                "n_fills": 0, "has_close": False}
    ordered = sorted(fills, key=fill_time_ms)

    def _num(f, key):
        try:
            return float(f.get(key) or 0)
        except (TypeError, ValueError):
            return 0.0

    gross = sum(_num(f, "closedPnl") for f in ordered)
    fees = sum(_num(f, "fee") for f in ordered)
    closing = [f for f in ordered if is_closing_fill(f)]
    if not closing:
        return {"net_pnl": None, "gross_pnl": gross, "fees": fees, "exit_px": None,
                "n_fills": len(ordered), "has_close": False}
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
        "has_close": True,
    }


def attribute_position_fills(wfills, tr: dict, opened_ts_ms: int):
    """Select the fills belonging to ONE position — contamination-proof.

    The naive "every same-coin fill since opened_at-buffer" approach lets a PRIOR
    or ADJACENT same-asset trade's fills leak into this position's realized PnL
    (a -$20 loss recorded as a +$30 win when the previous trade's +$50 close sat
    in the buffer). Strategy, in order:

      1. Closing fills whose oid matches this trade's known exit orders
         (tp_oid/tp1_oid/tp2_oid/sl_oid, and entry_oid if present) — oids are
         globally unique, so nothing from another trade can match.
      2. plus the single entry fill nearest ``opened_ts_ms`` (to capture the
         entry fee), if within 2 minutes of the open.
      3. FALLBACK, only when step 1 found no closing fill (manual/liquidation
         close with no tracked oid): dir-based closing fills at/after
         ``opened_ts_ms`` — but ONLY if the asset was not re-opened afterward
         (which would make attribution ambiguous). On a detected re-open we do
         NOT guess; the caller then sees has_close False and alerts.

    ``opened_ts_ms`` must be > 0 (the caller guards the unparseable case).
    Returns the attributed fills (possibly with no closing fill → caller alerts).
    """
    asset = tr.get("asset")
    exit_oids = {
        str(tr.get(k)) for k in ("tp_oid", "tp1_oid", "tp2_oid", "sl_oid", "entry_oid")
        if tr.get(k) is not None
    }
    coin_fills = [f for f in (wfills or []) if fill_coin_matches(f, asset)]

    matched = [f for f in coin_fills if str(f.get("oid")) in exit_oids]
    matched_oids = {str(f.get("oid")) for f in matched}
    has_oid_close = any(is_closing_fill(f) for f in matched)

    # (2) nearest entry fill for the entry fee
    entry_cands = [
        f for f in coin_fills
        if str(f.get("oid")) not in matched_oids and classify_fill(f, tr) == "entry"
    ]
    if entry_cands:
        nearest = min(entry_cands, key=lambda f: abs(fill_time_ms(f) - opened_ts_ms))
        if abs(fill_time_ms(nearest) - opened_ts_ms) <= 120_000:
            matched.append(nearest)
            matched_oids.add(str(nearest.get("oid")))

    # (3) dir-based fallback, only if no oid-matched close and no re-open
    if not has_oid_close:
        post = [f for f in coin_fills if fill_time_ms(f) >= opened_ts_ms]
        reopened = any(
            classify_fill(f, tr) == "entry"
            and str(f.get("oid")) not in matched_oids
            and fill_time_ms(f) > opened_ts_ms + 5000
            for f in post
        )
        if not reopened:
            for f in post:
                if is_closing_fill(f) and str(f.get("oid")) not in matched_oids:
                    matched.append(f)
                    matched_oids.add(str(f.get("oid")))
    return matched


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
