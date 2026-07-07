# Hyperliquid Trading Agent — 10x Improvement Plan

**Goal:** Fund the account with 100 USDC and net **+$10 (+10%) in the first week**, live from day one, with risk-taking that is *informed* — every dollar of risk backed by data the bot can actually see, and every mechanical leak that currently loses money closed before launch.

**Method:** This plan is built from a full forensic audit of the codebase (~5,100 lines), all 78 commits, and the complete run logs from May 16–30, 2026 (1,766 LLM decision cycles, 7,740 diary events, 3,405 operational log lines, 7 recorded trades). Every claim below cites its evidence.

---

## Table of Contents

1. [Diagnosis — why the bot loses money](#1-diagnosis)
2. [Goal math — what +$10/week actually requires](#2-goal-math)
3. [Phase 0 — Day-0 blockers (bot cannot run today)](#3-phase-0)
4. [Phase 1 — The Truth Layer (see reality)](#4-phase-1)
5. [Phase 2 — Execution correctness (stop mechanical bleeding)](#5-phase-2)
6. [Phase 3 — Economics (fees, funding, LLM cost)](#6-phase-3)
7. [Phase 4 — Strategy & LLM decision redesign (build edge)](#7-phase-4)
8. [Phase 5 — Risk envelope for $100 → +$10](#8-phase-5)
9. [Phase 6 — Verification pipeline (tests, dry runs, canary)](#9-phase-6)
10. [Week-one live playbook](#10-week-one-playbook)
11. [New configuration reference](#11-config-reference)
12. [Code-review & change-management guidelines](#12-code-review-guidelines)
13. [Honest expectancy math & risk disclosure](#13-expectancy)

---

<a name="1-diagnosis"></a>
## 1. Diagnosis — why the bot loses money

The last run: **$266.91 → $264.88 over 14 days (−0.8%)**, 96.9% hold rate, and **zero trades in the final 8 days**. The bot did not lose to bad market calls. It lost to nine structural defects, in order of severity:

### D1. The bot cannot see its own trades (CRITICAL)
- `get_recent_fills` returns `fills[-limit:]` on a **newest-first** userFills response (`hyperliquid_api.py:397-419`) — once the account has >100 lifetime fills, it returns the *oldest* fills. Consequence: reconcile PnL recovery finds nothing (`main.py:515-517`), TP1 fill detection never confirms (`main.py:743-747`), and the LLM's "recent fills" context is ancient.
- Entries are stamped `filled: true` while the order result says only `resting` (all 7 logged trades). Positions the exchange closed via TP/SL are discovered 2+ cycles later as `reconcile_close` with `pnl=null` — **35 times**, including the bot's single biggest win (+$0.59 SILVER TP hit, never recorded).
- **The doom loop:** the P3.3 performance-memory gate then reads a trade log where wins are invisible (`pnl=null`) and losses are recorded, computes "0% win rate over 2 trades," and **blocks all entries for 9 straight days** (cited in 517 hold rationales). The bot punished itself for winning.

### D2. Silent LLM outages ate 41% of a trading week (CRITICAL)
- 304 cycles (17.2% of the entire run, including one 69.3-hour continuous outage May 22–25) returned the literal reasoning `"tool loop cap"` — which is actually `except anthropic.APIError` swallowed at `decision_maker.py:339-343` and mislabeled. Forensic timing analysis (failed cycles completed in 3–5s vs 15–17s healthy) points to **Anthropic API credit exhaustion** — twice, matching ~$20 credit blocks at the measured spend rate.
- Spend rate: ~9,000 input tokens/cycle, **zero prompt caching** (`cache_read_input_tokens=0` on all 193 logged calls), ≈ $3.60–11/day — **the bot's inference bill was 2.5–4x its entire weekly profit target**, and running out of credits silently converted to "hold everything" with no alert.
- **As of today the bot is fully dead:** the default model `claude-sonnet-4-20250514` (`config_loader.py:81`) retired June 15, 2026. Every call now fails, and the thinking config shape (`budget_tokens`, `decision_maker.py:221-227`) is rejected with a 400 by all current models.

### D3. Execution paths that open naked, untracked positions (CRITICAL)
- All closes use `exchange.market_open` **without reduce-only** (`hyperliquid_api.py:207-243`): force-close, weekend close, exit-rule close, flip close. Any close computed from stale state can *open* an opposite position.
- Exit-rule closes use the **original** size, not `remaining_size` after a partial TP (`main.py:909` vs `:773`) — a post-TP1 exit sells 2x the position, opening a naked short.
- If TP/SL placement fails after a filled entry, the bot cancels orders and *forgets the position* without closing it (`main.py:1531-1543`) — 9 `tpsl_failed` events left positions unprotected; the final log state shows **2 live positions with `active_trades.json = []`**.
- The flip path is self-defeating: it market-closes the old position, records a cooldown, then its own cooldown gate rejects the re-entry — **10 of 10 flips after May 19** paid taker fees to go flat instead of flipping (`main.py:1184` → `risk_manager.py:540-543`).

### D4. Entry/exit design that mathematically guarantees churn (CRITICAL)
- Entries are triggered *by* transient 5m volume spikes ("volume spike 4.755"), then given exit rules requiring the spike to *persist* (`vol_spike_ratio_5m < 0.5`). Spikes mean-revert within minutes, so trades are mechanically killed 15–31 minutes after entry regardless of price. Two trades died at exactly 904–905 seconds = cooldown expiry + first evaluation.
- Realized result across the 7 logged trades: stated R:R of 1.5–4.2 was fiction; actual avg win $0.31 (inflated only by the *untracked* trade), avg loss $0.14, expectancy −$0.037/trade after fees. **Fees were 3x the gross trading loss.**
- 43% of sized entries were counter-trend scalps against the bot's own stated 4h structure — where the identifiable losses concentrated (ETH −$1.64, GOLD −$1.67). The one visibly successful pattern (with-trend BTC short held ~24h) was never repeated.

### D5. Gate stack compounds into deadlock (HIGH)
- Volume gate (cited in 43% of all hold rationales; computed on the **in-progress candle** so it's biased low early in every bar, `main.py:623-631`) + volatile-regime hard block (BTC/ETH classified "volatile" in 47/47 hourly refreshes — a de facto permanent ban, `risk_manager.py:551-553`) + performance-memory doom loop (D1) + cooldowns + weekend freeze = **96.9% holds and 8 final days at zero trades**. No gate has a decay or override path; several can never clear themselves.

### D6. Risk math is unanchored from reality (HIGH)
- Leverage is **never set on-exchange** (no `update_leverage` call exists) — margin, liquidation price, and force-close distance depend on whatever the account happens to have configured.
- Force-close triggers at 20% *of margin* = a 2% price move at 10x — tighter than every stop-loss, so it preempts the plan (`risk_manager.py:483-489`).
- The MIN_RR gate is vacuous: `main.py:1208-1218` injects a self-derived 2.5R TP2 into its own check, so R:R always evaluates to exactly 2.5 and never rejects anything.
- The $11 minimum-order bump *overrides* risk caps upward (`risk_manager.py:591-595`) — per-trade risk grows as the account shrinks.
- No weekly drawdown bound; the daily 25% breaker lives in memory only and resets on every restart (23 restarts in 14.7 days).

### D7. The LLM is asked to trade blind and its intents don't execute (HIGH)
- The decision schema has no `close`, `reduce`, or `update_sl` action — "sell" on a long forces a full flip (`main.py:1136-1198`). The prompt *instructs* thesis-decay closes and stop-tightening that the executor cannot perform; 20 hold rationales claim stop moves that never reached the exchange; 9 consecutive identical close attempts ran for 3 hours with zero awareness of failure.
- Funding rate and open interest shown to the LLM are **frozen at process start** (lifetime cache, `hyperliquid_api.py:594-617`) — ETH funding was quoted as exactly "9.62%" for 286 consecutive cycles and used as the deciding blocker each time.
- The prompt demands stops at "recent swing lows/highs" but contains **no candles and no swing structure**; orderbook data is fetched but never shown; fees appear in zero of 1,766 records; account equity glitched to $0.01 and the LLM accepted it ("Account nearly wiped").

### D8. No tests, no backtest, no dry-run — ever (HIGH)
- Zero test files in the entire git history. Every phase shipped at least one bug found only in production, including two consecutive releases that **crashed on every startup** (`17f74c3` NameError, `8c43fd6` UnboundLocalError). All verification was throwaway snippets pasted into commit messages. No commit ever cited realized PnL as evidence a change worked.

### D9. The trading host is exposed to the internet (HIGH — security)
- The status API binds `0.0.0.0:3000` (`config_loader.py:105`). The 48h agent.log shows **634 requests from 70 scanner IPs** (Shodan, Censys) probing `/.env`, `/cdp_api_key.json`, `/wp-config.php.bak` — credential hunts against a host holding the exchange signing key. Both process tracebacks in the log were scanner-induced.

---

<a name="2-goal-math"></a>
## 2. Goal math — what +$10/week actually requires

Facts the plan must respect (from live research):

| Constraint | Value |
|---|---|
| Hyperliquid base fees | 0.045% taker / 0.015% maker per side (HIP-3 xyz markets up to ~2x — verify live via perpDexs meta) |
| Minimum order notional | $10, API-enforced (partial exits below $10 rejected unless exact-close reduce-only) |
| Funding | settled hourly; cap 4%/hour in extremes; baseline drifts ~0.00125%/hr in favor of shorts |
| HIP-3 weekend behavior | oracle switches to EMA regime while underlying is closed; documented $13M liquidation event from one whale on a Sunday; uncompensated gap risk |
| Realistic expert returns | 5–15% per **month** — +10% in one week is an outlier outcome requiring concentrated risk |
| Previous run's costs | ~$0.028 fees/trade round-trip at $50 notional; $25–45/week LLM spend |

**The equation.** Net weekly PnL = (trades × expectancy) − fees − funding − LLM cost. Last run: (≈32 episodes × −$0.23) − ≈$3 fees − $25+ LLM = deeply negative before any edge question.

**The new operating point:**

| Lever | Old | New | Weekly impact |
|---|---|---|---|
| LLM cost | $25–45 | **≤ $3** (caching + cadence + model migration, §6) | +$22–42 |
| Fees | taker-heavy churn, ~30 min holds | maker entries, 2–4 trades/day max, multi-hour holds | fees ≈ $0.50–1.00/wk at target size |
| Risk per trade | ~$0.20 (unintentional) | **$1.50–2.50 by design** (1.5–2.5% of equity, confidence-tiered) | makes wins matter |
| Expectancy | −$0.037/trade | target **+0.30–0.35R/trade** = +$0.50–0.90 | +$8–13/wk at 15–18 trades |
| Uptime | 59% (outages + deadlock) | ≥ 99% with alerting | more shots on goal |

**Required performance for +$10:** ≈15–18 trades at avg risk $1.75, R:R ≥ 2:1 *net of costs*, win rate ≥ 45%. That is demanding but not fantasy — it requires with-trend entries, structural stops, letting winners reach 2R+, and zero mechanical leaks. Section 13 gives the honest probability assessment.

---

<a name="3-phase-0"></a>
## 3. Phase 0 — Day-0 blockers (before anything else)

*Timebox: half a day. Nothing else matters until these are done — the bot literally cannot run today.*

| # | Change | Files | Why |
|---|---|---|---|
| 0.1 | **Migrate the LLM.** Default `LLM_MODEL=claude-sonnet-5`; sanitizer path deleted (replaced by structured outputs in §6); replace `thinking={'type':'enabled','budget_tokens':N}` with `thinking={'type':'adaptive'}` (the old shape 400s on all current models). Do not set `temperature` (rejected on Sonnet 5). | `config_loader.py:81-88`, `decision_maker.py:221-227`, `.env.example` | `claude-sonnet-4-20250514` retired 2026-06-15 — every call fails. |
| 0.2 | **Bind the status API to localhost.** `API_HOST=127.0.0.1` default; move HTTP access logs to a separate file; if remote dashboard access is needed, use an SSH tunnel. | `config_loader.py:105`, `main.py` server setup | Scanners are actively probing the box that holds the signing key (agent.log:254, :345). |
| 0.3 | **Error honesty.** Split the `"tool loop cap"` label into `api_error:<class>:<status>`, `empty_response`, `tool_loop_exhausted`; persist the exception text into decisions.jsonl and the diary; remove the bare `except: pass` around decisions writes (`main.py:1030-1034`). | `decision_maker.py:339-343, 449-461` | 41% of a trading week was lost to a mislabeled billing failure nobody could see. |
| 0.4 | **Degenerate-cycle circuit breaker + alert.** After 3 consecutive error-holds: send an alert (see 0.6) and log CRITICAL. After 8: exit non-zero so the supervisor restarts and the operator is paged. Policy for "LLM down while positions open": verify on-exchange SL exists on every open position; if any missing, flatten. | new `src/watchdog.py`, wired in `main.py` | The 69-hour outage self-served hold-all silently; burst 1 happened *with an open position*. |
| 0.5 | **Anthropic billing runway.** Pre-fund the API account for ≥ 4 weeks at the new (post-§6) burn rate; set a console spend alert; log cumulative token spend per cycle into decisions.jsonl next to `account_value`. | `decision_maker.py` usage logging | Credit exhaustion is the root cause of the biggest outage. |
| 0.6 | **Push alerting channel.** One tiny `notify(msg, level)` helper posting to a Telegram bot / ntfy.sh topic. Wire it to: error-hold streaks, tpsl_failed, naked-position detection, daily-loss-limit trips, heartbeat gaps > 2 cycles, spend threshold. | new `src/notify.py` | Every failure class in the audit ran for hours-to-days because nothing paged a human. |
| 0.7 | **Startup config fingerprint.** On boot, log: git SHA, model ID, interval, assets, every gate flag, account equity, open positions. Refuse to start if `ASSETS`/`INTERVAL` missing. | `main.py` startup | 23 restarts whose configuration had to be reverse-engineered from inter-record gaps. |
| 0.8 | **Fix balance sanity.** Read `marginSummary.accountValue` (currently reads a nonexistent top-level key → always 0, `hyperliquid_api.py:461`); include negative PnL in fallback equity (`:536-537`); refuse to trade any cycle where equity deviates >50% from last-known without a matching fill/transfer (the $0.01 flap). | `hyperliquid_api.py`, `risk_manager.py` | Sizing math ran on garbage equity multiple times on day one. |

**Verification for Phase 0:** one live loop cycle in DRY_RUN (§9) shows: correct equity, model responds, structured decision parsed, no writes to exchange; kill the API key temporarily → alert fires within 3 cycles.

---

<a name="4-phase-1"></a>
## 4. Phase 1 — The Truth Layer

*Goal: the bot's ledger matches the exchange to the penny, always. Every downstream feature — performance memory, sizing, LLM context, the +$10 accounting itself — depends on this.*

| # | Change | Files | Evidence |
|---|---|---|---|
| 1.1 | **Fix fills ordering & pagination.** `userFills` is newest-first: take `fills[:limit]`, not `fills[-limit:]`; better, use the `userFillsByTime` endpoint with a start timestamp = position open time. Delete the no-op `isBuy` filter (`main.py:502-507`) and parse `side`/`dir` correctly. | `hyperliquid_api.py:397-419`, `main.py:482-517` | Single root cause of the pnl=null epidemic and dead TP1 detection. |
| 1.2 | **Event-driven fill detection.** Subscribe to the `userFills` and `orderUpdates` websocket channels (SDK ships a manager). On any fill: classify it (entry / TP / SL / manual / liquidation) by oid match against tracked orders, compute realized PnL **from the fill record including its `fee` field**, write the trade_log row immediately, cancel the sibling TP/SL leg, update `active_trades`. The 15-minute poll becomes a *backstop reconciler*, not the primary mechanism. | new `src/fill_tracker.py`; `main.py` reconcile section | Every natural exit was discovered late; the winner was never booked; SL fills left phantom positions fed to the LLM for up to 28 hours. |
| 1.3 | **Order state machine.** An entry is `resting` until the exchange says `filled` (poll order status by oid / websocket orderUpdates). Never stamp `filled: true` on a resting order. A position exists only when clearinghouseState says so. Record **actual fill price** (not pre-order mid) as `entry_price`; all R-multiples, TP ladders, breakeven and trailing math anchor to it. | `main.py:1331-1446, 1565` | `filled:true`-on-resting is the root of the phantom-position pathology; every R-multiple in the system is currently anchored to the wrong price. |
| 1.4 | **Complete trade_log.** Every close path writes a row (the May-16→20 gap and post-May-22 entries currently vanish); schema adds: `actual_entry_px, actual_exit_px, fees_paid, funding_paid, leverage, close_reason, oid_chain, thesis_id`. `pnl=null` becomes structurally impossible — if PnL can't be computed from fills, that's an alert, not a null. | `main.py` trade_log writers | trade_log captured 7 of ~73 opens (~10% of reality); performance memory learns from corrupted data. |
| 1.5 | **Cycle-start invariant check.** Diff `active_trades` vs clearinghouse positions vs open orders every cycle. Mismatch → adopt exchange truth, re-place missing SL immediately, alert. An asset whose position vanished without a recorded close is **halted** until fills history explains where it went (kills the 17-entry xyz:CL churn loop). | `main.py:454-545` | 35 blind reconciles; 13+ open/vanish/reopen round-trips in 2 hours on a weekend-frozen market. |
| 1.6 | **Performance memory reads exchange truth.** Rebuild per-asset stats from the (now complete) trade_log net of fees; count TP fills as wins. Replace the "0% win rate over 2 trades → permanent block" rule with decaying size modulation (§8.6). | `main.py:1701-1732` | The gate blocked the bot's only profitable pattern for 9 days on a 2-sample fiction. |

**Verification:** testnet integration test — open a tiny position, let a TP trigger fill, assert: trade_log row appears within one cycle with correct pnl/fees, sibling SL cancelled, active_trades empty, no reconcile_close event. Property test: replay recorded May fills through the classifier; assert every one of the 7 historical trades reconstructs with non-null PnL matching the forensic values (T1 −$0.075 … T5 +$0.59).

---

<a name="5-phase-2"></a>
## 5. Phase 2 — Execution correctness

*Goal: no code path can create an untracked or unprotected position; every intended action either completes or rolls back loudly.*

| # | Change | Files | Evidence |
|---|---|---|---|
| 2.1 | **All closes are reduce-only.** Add `market_close(coin, size=None)` using the SDK's reduce-only path sized from *live* position size fetched at call time. Force-close, weekend close, exit-rule close, flip close all use it. A "close" can then never open anything. | `hyperliquid_api.py:207-243`, call sites `main.py:347, 401, 911, 1145` | Closes via non-reduce-only `market_open` + stale state = naked opposite positions (observed on 05-18: 5 xyz:CL flips in 3.6h each labeled "MANDATORY CLOSE"). |
| 2.2 | **Atomic entry brackets.** After a confirmed entry fill: place TP + SL; if **either** fails after 2 retries with backoff → flatten the position immediately and alert. Use TP/SL sized to position with reduce-only trigger orders; verify both oids exist before registering the trade. Never set `tp1_filled=True` unless the replacement SL actually placed (`main.py:772`). | `main.py:1449-1543, 708-792` | 9 tpsl_failed events, 6 leaving fully naked leveraged positions; H8 path leaves filled entries permanently invisible. |
| 2.3 | **Place-before-cancel for stop updates.** Trailing/breakeven SL moves place the new SL first, then cancel the old (two resting reduce-only stops momentarily is safe; zero stops is not). Fix the "keeping old" log lie (`main.py:890`). | `main.py:752-771, 852-890` | Cancel-then-place leaves unprotected windows; one failure path leaves the remainder permanently stopless. |
| 2.4 | **Fix the flip.** Execute as one state machine: reduce-only close → confirm flat → open new side → brackets — with the cooldown recorded only *after* the new entry succeeds, and flips exempt from their own close-cooldown. Invariant: a flip decision ends with either a position on the new side or the old position intact — never flat. If any step fails, stop and alert. | `main.py:1136-1198`, `risk_manager.py:255-291` | 10/10 flips after May 19 half-executed: paid fees to go flat. |
| 2.5 | **Limit-entry chase policy.** Post-only join at best bid/ask, re-quote to the touch every 10s; poll **position size** during the window (catches partial fills — currently unchecked, `main.py:1343-1345`); at 90s, if the setup's trigger condition still holds, cross with an IOC capped at 0.25% slippage on majors (skip on HIP-3); on partial fill at timeout, keep the filled part and bracket it. Log fill-rate and decision-price-vs-fill slippage per asset. | `main.py:1258-1398` | 15 of ~27 limit attempts timed out/rejected; static post-only joins fill on adverse selection (losers fill, winners run away unfilled). |
| 2.6 | **Set leverage explicitly.** At startup, `update_leverage` per asset: 3x isolated on majors, 2x isolated on HIP-3. Record actual leverage in every trade row. Force-close redefined in equity terms (§8.4). | `hyperliquid_api.py` (new), `main.py` startup | Leverage never set → liquidation and force-close distances are whatever the account happened to have. |
| 2.7 | **Async LLM call.** Wrap the Claude call in `asyncio.to_thread` (or AsyncAnthropic) so exits/trailing/watchdog/API keep running during the 8–35s decision latency; execution re-validates against **fresh** state after the call returns (kills the stale-state race at `main.py:1105-1147`). | `main.py:979`, `decision_maker.py:25` | Event loop freezes for the whole LLM round-trip; execution decisions run on minutes-stale state. |
| 2.8 | **Refactor for testability.** Extract from the 1,766-line `main.py`: `execution.py` (entry/exit/flip state machines), `position_manager.py` (brackets, trailing, partial TP), `reconciler.py`. Explicit dataclass state instead of `nonlocal`. This is the enabler for the §9 test suite — refactor only these execution-critical paths now; cosmetics later. | `src/main.py` → new modules | The nonlocal-monolith shape directly produced two crash-on-startup releases (17f74c3, 8c43fd6). |

**Verification:** testnet scenario suite (§9.3) runs every state machine end-to-end: entry-fill→bracket, TP-fill→sibling-cancel, SL-fail→flatten, flip, partial-fill-timeout, exit-rule-after-TP1 (asserts reduce-only remainder close — the D3 2x-size bug becomes a permanent regression test). Chaos test: kill the process between entry fill and bracket placement; on restart the reconciler must find the naked position and bracket or flatten it within one cycle.

---

<a name="6-phase-3"></a>
## 6. Phase 3 — Economics: fees, funding, and LLM cost

*Goal: the bot knows what every action costs before it acts, and its own overhead is < $3/week.*

### 6.1 Fee & funding awareness (in code, not just prompt)
- Build a per-asset cost model refreshed each cycle: `maker_bps, taker_bps` (read HIP-3 dex fee config live from the meta API — xyz markets may be ~2x core), current spread from `get_orderbook` (already implemented, currently unused for this), expected funding over intended hold (funding is fetched but **frozen** — fix the lifetime `metaAndAssetCtxs` cache to a per-cycle refresh of asset contexts, keeping only szDecimals static, `hyperliquid_api.py:594-617`).
- **EV gate replacing the vacuous MIN_RR:** validate the LLM's own TP/SL: `p_win·reward − p_loss·risk − roundtrip_cost − expected_funding > 0` with p from confidence tier, and require `|entry−TP| ≥ 4×(spread + fees)`. Delete the self-derived tp2 injection (`main.py:1208-1218`).
- All recorded PnL becomes net-of-fees (fills carry the `fee` field); breakeven SL is placed at entry ± 2×fees, not raw entry (`main.py:763-767`).
- Forbid holding through funding > 0.1%/hr against the position (checked cycle-start, uses live funding).

### 6.2 LLM cost: from ~$30/week to ~$2/week

| Lever | Detail | Est. effect |
|---|---|---|
| Model | `claude-sonnet-5` ($3/$15 per MTok; intro $2/$10 through Aug 2026) for the decision loop. Optional: escalate single decisions to `claude-opus-4-8` ($5/$25) when proposed risk >2% equity — a per-trade quality upgrade costing ~1¢. Never Haiku for decisions (calibration matters); Haiku 4.5 acceptable for the pre-filter below. | ~40% cheaper than old Sonnet 4 pricing, vastly better model |
| Prompt caching | `cache_control: {type: "ephemeral", ttl: "1h"}` on the static system prompt + tool defs. **Must be 1h TTL** — the 5-minute default expires between 15–30m cycles and would make every call a paid cache *write* with zero reads. Keep the system prompt byte-stable (no timestamps/interpolation); volatile market data goes last in the user turn. Verify `cache_read_input_tokens > 0` in cycle 2 as a startup assertion. | ~80–90% off input cost |
| Structured outputs | `output_config.format` json_schema (or `client.messages.parse()` with a Pydantic model) — schema-guaranteed JSON in one call. Delete the Haiku sanitizer, the fence-stripping parser, and the full-context retry (which doubled cost exactly when failing). Kills the 2.7% parse-failure class entirely. | −1 model, −retries |
| Cadence | Baseline decision call every 30m. Event triggers force an immediate call: price crosses tracked SL/TP ±0.5%, vol spike >3x, funding flip, regime change, position age > thesis horizon. Skip the LLM entirely when *all* assets are gated/frozen and flat (a deterministic pre-check — the bot spent whole weekends generating 2,000-char analyses of frozen markets); rules-only management (exits, trailing, brackets) keeps running every 5m without the LLM. | ~50–75% fewer calls, faster on what matters |
| Spend telemetry | Per-call tokens×price accumulated in decisions.jsonl; daily line: LLM cost vs realized PnL; alert at $1/day. | prevents D2 recurring |

Estimated: ~40 calls/day × (~1k uncached input + ~8k cached-read + ~400 output) ≈ **$0.30–0.45/day ≈ $2–3/week**.

**Verification:** unit test asserting the system prompt renders byte-identical across two consecutive cycles; live assertion on `cache_read_input_tokens`; a `pytest` EV-gate table test (fee/spread/funding scenarios × confidence tiers → accept/reject); one dry-run day's actual measured spend < $0.50.

---

<a name="7-phase-4"></a>
## 7. Phase 4 — Strategy & LLM decision redesign

*Goal: give the model the information and the vocabulary to be genuinely confident, and make code enforce what prompts cannot. Git history proves prompt guidance alone does not constrain the LLM (`d84c99a`: it wrote "catastrophically weak volume" and traded anyway).*

### 7.1 Decision schema v2 (executor implements every field)

```json
{
  "asset": "BTC",
  "action": "open_long | open_short | close | reduce | update_sl | update_tp | hold",
  "confidence": 0.0-1.0,
  "risk_pct": 0.5-2.5,
  "entry": {"type": "limit|market", "px": null},
  "sl_px": 0.0, "tp_px": 0.0,
  "thesis": "one sentence, must be a PERSISTENT condition (structure/level/funding), not a transient reading",
  "invalidation": "price/structure condition that falsifies the thesis",
  "horizon_bars": 8,
  "size_pct_for_reduce": null,
  "rationale": "…"
}
```

- `close`/`reduce`/`update_sl`/`update_tp` become real, idempotent executor actions (fixes D7: today "sell" on a long force-flips; stop-tightening intents are no-ops). Once a close is issued, the *executor* owns completion — the asset is marked `closing` in the next prompt instead of being re-analyzed (kills the 9-consecutive-identical-close loops).
- Ambiguous `buy`/`sell` vocabulary is deleted. Holds carry no allocation. One decision per asset, validated on receipt; missing/malformed → re-ask once with only the malformed fragment.
- `confidence` is mandatory and *drives sizing* (§8.2) — closing the loop the owner asked for: risk-taking justified by stated confidence, graded weekly against outcomes (calibration report: win rate per confidence bucket).

### 7.2 Context v2 — what the model sees every cycle
1. **Structure it's asked to trade:** last 30×5m + 30×1h + 30×4h compact OHLCV, precomputed swing highs/lows, and nearest S/R levels (the prompt already *demands* swing-anchored stops; today the model sees only indicator scalars and 10 mids).
2. **Cost card per asset:** maker/taker bps, live spread, top-of-book depth, expected round-trip cost in $ at intended size, current + trailing-24h funding. (Fees appear in **zero** of 1,766 historical records.)
3. **Live funding/OI with deltas** (post-cache-fix), flagged STALE if unchanged >4 cycles — the model accepted a 32-hour-frozen funding value as the deciding factor 286 times.
4. **Gate states as structured data:** cooldown seconds remaining, regime label + whether gate active, volume ratio vs threshold, daily-loss budget left — so it stops wasting cycles proposing trades that code will veto (14 min_rr rejections, constant cooldown arithmetic in prose).
5. **Execution feedback:** last cycle's orders with outcomes (filled/rejected/slippage), so claimed actions match reality.
6. **Honest performance memory:** per-asset net-of-fee stats from the truth layer, size-weighted, time-decayed, with sample counts.
7. **Macro calendar:** static weekly file of FOMC/CPI/NFP/EIA timestamps; code enforces a no-new-entry window ±30min around events for correlated assets (the bot trades gold, oil, and index perps whose main risk is scheduled releases).

### 7.3 Strategy rules (enforced in code)
- **Trend alignment default:** entries must align with 4h structure (price vs EMA20/50 + regime label). Counter-trend requires `confidence ≥ 0.8`, half size, and a structural level (43% of old entries were counter-trend scalps; that's where the losses lived).
- **Ban transient-signal theses:** entry theses referencing vol-spike or single-bar signals are rejected by a code check on the `thesis` field; volume may *time* an entry, never *be* the thesis. Exit rules on mean-reverting instantaneous indicators (`vol_spike_ratio`, 5m MACD sign) are removed from the whitelist; allowed exit-rule forms: price-level, structural (4h EMA/swing), `max_bars_held` (new — implements S8 thesis decay honestly), funding threshold.
- **Bracket-first management:** the default trade is entry + TP + SL + `max_bars_held`, untouched by the LLM until either fills or invalidation triggers. Evidence: the one unmanaged trade (+1.68% on notional) beat all six managed ones combined. LLM management actions are for *adding* to the plan (trail after 1.5R, take partial into strength), not for panic-closing at the first red candle.
- **Exit-rule validation at entry:** every rule's indicator name is validated against snapshot keys — a typo today creates a permanently dead rule with zero warnings (`exit_evaluator.py:57-59`).
- **Per-asset trade budget:** max 2 entries/asset/day, max 5 total/day (code gate) — kills the 12-reversals-in-11-hours BTC churn while leaving room for the 15–18 quality trades/week the goal needs.
- **Asset roster:** BTC, ETH, SOL (deep books, <1bp spreads, 24/7 true trading) as the core; xyz:GOLD/xyz:SILVER allowed **only** during COMEX hours with flat-before-weekend enforcement (§8.7) and live-verified fee config. Drop xyz:CL and equities for week one (the CL churn loop + the SP500 orphan are both in the logs).
- **Regime gates become playbook selectors, not bans** (§8.6): "volatile" halves size and requires structural confirmation instead of hard-rejecting (BTC/ETH sat classified volatile for 47/47 refreshes = a permanent ban under the old rule; expanding vol is also where 2R+ targets actually get hit).

### 7.4 Prompt & model hygiene
- Rewrite the system prompt to match reality: remove dead `order_type`/`limit_price` teaching (executor ignores them today), the false ATR-multiplier claims (`decision_maker.py:43` says 30%/20%; code does 0.5x/1.0x), self-imposed cooldown prose (code owns cooldowns), and tool-calling instructions while `ENABLE_TOOL_CALLING=false` (decide: enable with per-iteration logging + cap alerts, or delete the loop — its only historical effect was mislabeling API errors).
- One authoritative threshold per gate, injected into the prompt from config (the volume gate is 0.5 in code and 0.7 in prose today; thresholds in prose drift — "R:R 0.89 meets minimum" got accepted).
- Anti-flip-flop: the previous cycle's thesis + invalidation are echoed back; changing direction requires the stated invalidation to have triggered, or `confidence ≥ 0.8` (37 same-asset direction flips within 2h in the old logs).

**Verification:** replay harness (§9.2) A/B: old prompt vs new prompt over the recorded 14-day market data — measure decision distribution, gate-rejection rate, hypothetical bracket-only PnL. Schema validation unit tests. Calibration report generated from week-one paper trades before live scale-up.

---

<a name="8-phase-5"></a>
## 8. Phase 5 — Risk envelope for $100 → +$10

*Principle: bounded dollar risk per trade, more capital efficiency than today's accidental 0.8x gross cap, and no gate that can deadlock. Risk-taking is allowed; unbounded or unpriced risk is not.*

| # | Rule | Value / formula |
|---|---|---|
| 8.1 | **Position sizing** | `size = (equity × risk_pct) / |entry − SL|`, leverage only to satisfy min-notional and margin efficiency. Risk is defined at the stop, not by notional. |
| 8.2 | **Confidence → risk ladder** | conf 0.55–0.65 → 1.0% ($1); 0.65–0.8 → 1.5%; ≥0.8 → 2.5% ($2.50 — the "informed aggression" tier the owner wants). Below 0.55: no trade. Enforced in code; the LLM's `risk_pct` request is clamped to its tier. |
| 8.3 | **Exposure caps** | Max 3 concurrent positions; max gross notional 150% of equity (real leverage headroom instead of today's meaningless 0.8x cap + 10 position slots that arithmetic makes unplaceable); correlated groups (GOLD+SILVER; BTC+ETH+SOL same-direction) count as 1.5 positions and share a 3.5% combined risk budget. |
| 8.4 | **Force-close backstop** | Redefined: close when unrealized loss ≥ 1.5 × planned SL distance (backstop for a failed/gapped stop), or position loss ≥ 3% of *equity*. Never denominated in margin (today's 20%-of-margin fires at a 2% price move and preempts every stop). |
| 8.5 | **Loss limits (persisted to disk, restart-proof)** | Daily: stop new entries at −5% ($5), flatten and halt at −6%. Weekly: halt at −15% from week-start equity, manual reset required. High-watermark and breaker state in a state file, not memory (today a restart silently clears an active breaker mid-drawdown). |
| 8.6 | **Deadlock-proof gates** | Every gate must have a decay or override: performance-memory modulates size (0.5x after 3 net-losing trades on an asset, decays over 20 trades — never a permanent 2-sample block); regime=volatile → 0.5x size + structural confirmation (not a ban); cooldown 1 bar after wins, 3 after losses, waived for flips; volume gate computed on the **last closed candle** (`main.py:616-631` currently uses the in-progress bar, biasing it toward blocking early in every bar) and used as a timing filter only. Meta-rule: if the bot has been 100% blocked for 12h while markets are open, alert the owner with the blocking breakdown. |
| 8.7 | **HIP-3 policy** | Trade xyz:GOLD/SILVER only while COMEX is open; flat 1h before Friday close (enforced, not prompted); no weekend holding (documented oracle-EMA manipulation risk — $13M liquidated by one Sunday whale); position entry blocked when oracle frozen (pre-trade gate, not post-hoc close — the bot once bought oil 17 times into a frozen weekend). |
| 8.8 | **Min-notional honesty** | If risk-based size < $10 notional, use leverage to keep margin at intended risk while clearing $10; if the setup can't clear min-notional within its risk budget, **reject** — never bump size up (today the $11 floor overrides every cap and grows risk as equity shrinks). Partial TP legs below $10 notional collapse into a single TP (exchange rejects sub-$10 non-exact reduces). |
| 8.9 | **Drawdown-responsive throttle** | Equity < $95: risk ladder ×0.75. < $90: ×0.5, max 1 position, majors only. Recovery to $97: restore. Mirrors aggression to the remaining runway. |

**Verification:** property-based tests (hypothesis): for random equity/SL/confidence, assert dollar-risk-at-SL ≤ ladder cap, notional ≥ $10 or rejected, gross ≤ 150%. Monte Carlo on the target profile (45% win, 2:1, 1.5% risk, 18 trades): report P(week ≥ +$10), P(week ≤ −$10), P(breaker trip) — publish in the repo so expectations are on record. Restart-resilience test: trip the daily breaker in paper mode, kill the process, restart, assert still tripped.

---

<a name="9-phase-6"></a>
## 9. Phase 6 — Verification pipeline

*The git history's core lesson: every phase shipped production bugs because verification was throwaway. This phase is infrastructure, committed and CI-enforced. Nothing from Phases 1–5 goes live except through this funnel.*

### 9.1 Unit test suite (`tests/`, pytest, runs in CI on every PR)
- **Indicators:** parity vs pandas-ta reference on fixture candles (catches the existing ADX off-by-one at `local_indicators.py:296-307`); explicit last-closed-candle semantics; falsy-zero funding/OI regression (`0.0 ≠ None`, `hyperliquid_api.py:637, 752`).
- **Risk manager:** sizing ladder, exposure caps, min-notional reject-not-bump, EV gate tables, force-close thresholds, breaker persistence, gate decay curves.
- **Execution state machines:** entry→bracket, TP-fill→sibling-cancel, SL-fail→flatten, flip atomicity, partial-fill handling, exit-rule-after-TP1 sizing — against a mocked exchange with fault injection (rejections, timeouts, partial fills, crash-restart).
- **Decision layer:** schema validation, per-asset coverage, thesis-persistence lint, prompt byte-stability (cache), threshold consistency between config and rendered prompt.
- **Fill classifier:** replay of the recorded May fills reconstructing all 7 historical trades to the forensic PnL values.

### 9.2 Replay & backtest harness (`tools/replay.py`)
- **Data:** decisions.jsonl already contains full indicator snapshots every ~15min for 14 days; plus Hyperliquid candleSnapshot history fetched on demand.
- **Mode A — logic regression:** re-run gates/exit-rules/sizing over recorded snapshots; every code change must show its decision-distribution diff ("this change would have blocked 3 more entries, released 41 frozen cycles") before merge.
- **Mode B — strategy EV:** simulate bracket-only execution over historical candles with the fee model (maker entry, trigger exits, slippage haircut); acceptance criterion for any strategy/config change: **expectancy after costs over the replay period must not degrade.** First experiment already queued: the T5 hypothesis (bracket-only management, no indicator exits) vs. historical managed behavior.

### 9.3 Dry-run ladder (each step gates the next)
1. **`DRY_RUN=true` paper mode (new):** full production loop against live mainnet data; orders go to a simulated fill engine (fill at touch when price crosses, maker/taker fees applied, spread haircut); writes the same logs/dashboards. **Minimum 48h clean** — zero invariant violations, zero unexplained holds, measured LLM spend < $0.50/day, ≥ 3 simulated round-trips with to-the-penny accounting.
2. **Testnet (`HYPERLIQUID_NETWORK=testnet`):** the §5 execution scenario suite against the real API — validates order plumbing, trigger fills, reduce-only semantics, fills-endpoint ordering, rate-limit behavior. Run once per release that touches execution code.
3. **Live canary (day 1):** min-size ($10–12 notional, risk ladder ×0.5, max 2 concurrent), majors only. Success = every fill, fee, and PnL cent reconciles against exchange fill history at day's end, all alerts quiet.
4. **Scale-up (day 2+):** full risk ladder per the playbook (§10).

### 9.4 CI & monitoring
- **GitHub Actions:** pytest + one full mocked-exchange loop cycle + replay Mode A diff on every PR. A PR fails if the bot cannot complete one simulated cycle (would have caught both historical crash-on-startup releases).
- **Runtime monitoring:** heartbeat line every cycle (equity, positions, spend, last-decision age); watchdog alerts (§3); nightly job diffing trade_log cumulative PnL vs on-chain equity change (bookkeeping drift caught in 24h, not never); weekly calibration + expectancy report auto-generated from trade_log.

---

<a name="10-week-one-playbook"></a>
## 10. Week-one live playbook ($100, day by day)

**Pre-week (build + burn-in):** Phases 0–2 implemented → unit suite green → 48h paper run clean → testnet suite green → Phases 3–5 config frozen → **sign-off checklist** (every §12 invariant demonstrated once).

| Day | Mode | Risk | Objective | Abort/adjust triggers |
|---|---|---|---|---|
| 0 | Fund 100 USDC; set referral code before first trade (free 4% taker discount); verify balance reads exactly; leverage set per asset; alerts test-fired | — | Clean start state | Balance misread → do not start |
| 1 | Canary live | ladder ×0.5, max 2 positions, BTC/ETH/SOL only | ≥1 complete round-trip; **accounting reconciles to the cent**; spend < $0.50 | Any invariant violation → halt, fix, restart canary |
| 2–3 | Full ladder | 1–2.5% per trade | 2–4 quality trades/day; target +$2–4 cumulative | Day ≤ −$5 → next day at ×0.75; 3 consecutive stop-outs → 12h pause + review theses vs replay |
| 4–5 | Full ladder + xyz:GOLD/SILVER during COMEX hours if majors are quiet | same | +$5–8 cumulative | Weekly ≤ −$10 → drop to ×0.5 and majors only |
| 5 (Fri) | **Flat all HIP-3 by 1h before COMEX close** (enforced) | — | No weekend gap exposure | — |
| 6–7 | Majors only (24/7 books) | If cumulative ≥ +$8: ladder ×0.75 (protect the win). If +$4–8: normal. If < +$4: normal, never escalate to "catch up" | Land ≥ +$10 | Weekly breaker −15% = hard stop |
| 7 (Sun) | Week review: calibration report, expectancy by setup type, fee/funding/LLM cost totals vs plan | — | Decide week-2 parameters from data | — |

**Standing rule the owner asked for, made explicit:** the bot *is* allowed to take the 2.5% high-confidence trades — that's the design, not a violation. What it may never do is escalate size to chase a target ("revenge sizing"): the ladder is a ceiling keyed to confidence, and day-loss/week-loss stops override the +$10 goal, always.

---

<a name="11-config-reference"></a>
## 11. New configuration reference (delta from current `.env.example`)

```bash
# — Model / LLM (Phase 0 + 3)
LLM_MODEL=claude-sonnet-5            # was: claude-sonnet-4-20250514 (RETIRED 2026-06-15)
ESCALATION_MODEL=claude-opus-4-8     # decisions risking >2% equity (optional)
# SANITIZE_MODEL: deleted — structured outputs replace the sanitizer
PROMPT_CACHE_TTL=1h                  # 5m default TTL expires between cycles — must be 1h
DECISION_INTERVAL=30m                # LLM cadence; rules/exits loop stays at 5m
LLM_DAILY_SPEND_ALERT_USD=1.00

# — Security / ops (Phase 0)
API_HOST=127.0.0.1                   # was 0.0.0.0 — publicly scanned
ALERT_WEBHOOK_URL=...                # Telegram/ntfy; error streaks, naked positions, breakers
ERROR_HOLD_ALERT_AFTER=3
ERROR_HOLD_RESTART_AFTER=8

# — Risk (Phase 5) — replaces the loosened e766ad2 values
RISK_PCT_LOW=1.0                     # confidence 0.55-0.65
RISK_PCT_MID=1.5                     # confidence 0.65-0.8
RISK_PCT_HIGH=2.5                    # confidence >=0.8
MIN_CONFIDENCE=0.55
MAX_CONCURRENT_POSITIONS=3           # was 10 (unplaceable at $100)
MAX_GROSS_NOTIONAL_PCT=150           # was MAX_TOTAL_EXPOSURE_PCT=80 (capped account at 0.8x)
DAILY_SOFT_STOP_PCT=5                # was 25 (allowed losing 2.5x weekly target in a day)
DAILY_HARD_STOP_PCT=6
WEEKLY_STOP_PCT=15
LEVERAGE_MAJORS=3
LEVERAGE_HIP3=2
FORCE_CLOSE_EQUITY_PCT=3             # replaces MAX_LOSS_PER_POSITION_PCT (margin-denominated)
MAX_ENTRIES_PER_ASSET_PER_DAY=2
MAX_ENTRIES_PER_DAY=5

# — Strategy (Phase 4)
ASSETS="BTC ETH SOL xyz:GOLD xyz:SILVER"   # CL and equities dropped for week one
HIP3_SESSION_ONLY=true               # COMEX hours only, flat before weekend
COUNTER_TREND_MIN_CONFIDENCE=0.8
MAX_BARS_HELD_DEFAULT=16             # thesis time-stop (bars of DECISION_INTERVAL)
NEWS_BLACKOUT_MINUTES=30             # around FOMC/CPI/NFP/EIA per calendar file

# — Verification (Phase 6)
DRY_RUN=false                        # true = paper mode (required 48h before any live deploy)
```

Deleted (dead/superseded): `MIN_RR` (replaced by EV gate), `REGIME_GATE_VOLATILE` (playbook selector now), `MIN_VOL_SPIKE_RATIO` as entry-blocker (timing filter only), `TAAPI_API_KEY`, `OPENROUTER_API_KEY`, `LIGHTER_PRIVATE_KEY` fallback, `THINKING_BUDGET_TOKENS`.

---

<a name="12-code-review-guidelines"></a>
## 12. Code-review & change-management guidelines

**Runtime invariants — violation = alert + halt (checked every cycle, tested in CI):**
1. Every open position has a live SL order on-exchange (oid verified).
2. Every close order is reduce-only.
3. `active_trades` ≡ clearinghouse positions ≡ tracked orders (three-way diff).
4. No trade_log row may carry `pnl=null`.
5. Dollar-risk-at-SL of any position ≤ its confidence-tier cap; gross ≤ 150%.
6. A flip ends on the new side or unchanged — never flat.
7. Breaker/watermark state survives restart (file-backed).
8. `cache_read_input_tokens > 0` from the second call after boot.

**Per-PR checklist (in the PR template):**
- [ ] One feature/fix per PR (PR #15 bundled 9 features and needed two fix-PRs within days).
- [ ] Unit tests for the changed path; full suite green in CI.
- [ ] Replay Mode-A diff attached: what decisions change on the recorded 14 days, and why that's desirable.
- [ ] For strategy/risk changes: replay Mode-B expectancy-after-costs did not degrade.
- [ ] One full paper-mode cycle log attached (catches crash-on-startup class bugs).
- [ ] No new gate without a decay/override path and a "what unblocks this" log line.
- [ ] Any new threshold lives in config, is injected into the prompt from that config, and is asserted consistent in tests.
- [ ] New log/telemetry fields documented; README guard table updated (it currently documents nothing past Phase 2).
- [ ] Live-money changes: 24h paper burn-in after merge before the live deploy is promoted.

**Deploy protocol:** pin dependencies in the Dockerfile from the lockfile (today it pip-installs unpinned latest into a live-money bot); run as non-root; healthcheck on the (localhost) status endpoint; deploys land between trading sessions with the reconciler verifying clean state on boot.

---

<a name="13-expectancy"></a>
## 13. Honest expectancy math & risk disclosure

With every leak in this plan fixed, the +$10 week requires roughly: 15–18 executed trades, ≥2:1 realized R:R net of costs, ~45% win rate, ~$1.75 avg risk. For calibration:

- **P(hit +$10 in week 1):** with a genuinely break-even signal (50/50 at 2:1 gross ≈ 45% net), Monte Carlo puts a +$10 week at roughly **25–35%** — the plan's edge sources (with-trend filtering, structural stops, maker entries, funding tilt, letting winners run) have to be real for the odds to be better than that.
- **P(losing week):** bounded by design at −$15 (weekly breaker), with daily bleed capped at −$6.
- The *near-certainties*, unlike the target: LLM cost drops ~90%, fee drag drops ~70%, the bot stops deadlocking itself, stops losing track of positions, and produces trustworthy data — so even a flat week 1 yields the calibration data that makes week 2's risk genuinely informed. That is the honest 10x: not a guaranteed +10%, but a system where +10% weeks are *possible* and every week is survivable and diagnosable.

What was true before is worth stating once, plainly: the previous system could not have reached +$10/week under any market conditions — its own inference bill exceeded the target 3x, its gates converged to zero trades, and its accounting couldn't have told you if it *had* won.

---

## Implementation order & effort summary

| Phase | Scope | Est. effort | Gate to next |
|---|---|---|---|
| 0 | Model migration, security, alerting, error honesty, billing | 0.5 day | One clean dry cycle + alert test |
| 1 | Truth layer (fills, order states, trade_log, reconciler) | 1.5 days | Testnet fill-classification suite green; historical trades reconstruct |
| 2 | Execution correctness (reduce-only, brackets, flip, chase, leverage, async) | 2 days | Testnet scenario suite + chaos test green |
| 3 | Economics (EV gate, funding fix, caching, structured outputs, cadence) | 1 day | Spend < $0.50/day measured; EV-gate tests green |
| 4 | Strategy + schema v2 + context v2 + prompt rewrite | 2 days | Replay A/B report; schema tests green |
| 5 | Risk envelope | 1 day | Property tests + Monte Carlo published |
| 6 | Test suite, replay harness, paper mode, CI, monitoring | 2 days (parallel with 1–5) | 48h clean paper run |
| — | **Total to live canary** | **~7–8 focused days** | Week-one playbook §10 |
