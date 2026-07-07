"""Phase 2 (2.8): pure, testable execution helpers.

A full extraction of the execution state machine out of the 1900-line main.py is
too risky to do without live integration tests, so this module holds only the
side-effect-free DECISION logic that benefits most from unit tests — the bracket
price math (which now anchors to the actual fill price) and small guards. The
async I/O orchestration stays in main.py.
"""


def compute_bracket_prices(entry_px, sl_px, is_long, tp1_at_r, tp2_at_r, llm_tp=None):
    """Compute (tp1_price, tp2_price, r) from entry, SL and R-multiples.

    R = |entry - sl| is the unit of risk, anchored to the ACTUAL fill price
    (Phase 2 1.3). ``llm_tp`` overrides TP2 only when it is FARTHER than the
    R-based TP2 (so the model can extend, never shorten, the far target).

    Returns (None, None, 0.0) when inputs are invalid or R <= 0 — the caller then
    falls back to the legacy single-TP path.
    """
    try:
        entry_px = float(entry_px)
        sl_px = float(sl_px)
        tp1_at_r = float(tp1_at_r)
        tp2_at_r = float(tp2_at_r)
    except (TypeError, ValueError):
        return None, None, 0.0
    r = abs(entry_px - sl_px)
    if r <= 0:
        return None, None, 0.0
    if is_long:
        tp1 = entry_px + tp1_at_r * r
        tp2 = entry_px + tp2_at_r * r
        if llm_tp is not None:
            try:
                t = float(llm_tp)
                if t > tp2:
                    tp2 = t
            except (TypeError, ValueError):
                pass
    else:
        tp1 = entry_px - tp1_at_r * r
        tp2 = entry_px - tp2_at_r * r
        if llm_tp is not None:
            try:
                t = float(llm_tp)
                if t < tp2:
                    tp2 = t
            except (TypeError, ValueError):
                pass
    return tp1, tp2, r


def close_is_buy(is_long: bool) -> bool:
    """Direction of the order that CLOSES a position (long→sell, short→buy)."""
    return not is_long


def bracket_incomplete(orders_ok: bool, filled: bool) -> bool:
    """Whether a filled entry ended up without a complete protective bracket
    (→ the position must be flattened, never left naked)."""
    return bool(filled) and not bool(orders_ok)
