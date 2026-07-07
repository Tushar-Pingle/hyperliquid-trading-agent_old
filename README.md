# Hyperliquid AI Trading Agent

An AI-powered trading agent that uses Claude to analyze markets and execute perpetual futures trades on Hyperliquid. Supports crypto, stocks, commodities, indices, and forex via HIP-3 markets.

## What It Does

1. Fetches real-time candle data and computes technical indicators (EMA, RSI, MACD, ATR, BBands, ADX, OBV, VWAP) locally from Hyperliquid
2. Sends full market context to Claude, which decides buy/sell/hold for each asset
3. Executes trades with take-profit and stop-loss orders
4. Hard-coded safety guards enforce position limits, leverage caps, and loss protection

## Tradeable Markets

All 229+ Hyperliquid perp markets plus HIP-3 tradfi assets:

- **Crypto**: BTC, ETH, SOL, HYPE, AVAX, SUI, ARB, LINK, and 200+ more
- **Stocks**: xyz:TSLA, xyz:NVDA, xyz:AAPL, xyz:GOOGL, xyz:AMZN, xyz:META, xyz:MSFT, xyz:COIN, xyz:PLTR...
- **Commodities**: xyz:GOLD, xyz:SILVER, xyz:BRENTOIL, xyz:CL, xyz:COPPER, xyz:NATGAS, xyz:PLATINUM
- **Indices**: xyz:SP500, xyz:XYZ100
- **Forex**: xyz:EUR, xyz:JPY

## Safety Guards

All enforced in code, not just LLM prompts. Configurable via `.env`:

| Guard | Env var | Default | Description |
|-------|---------|---------|-------------|
| **Stacking block** | `STACKING_ALLOW_SCALE_IN` | `false` | Hard-blocks adding to an existing same-direction position |
| **Cooldown** | `COOLDOWN_BARS` | `3` | Bars of silence after any open/close/flip before the asset can trade again |
| **Limit entries** | `ENTRY_ORDER_TYPE` | `limit` | Post-only limit orders by default; set `market` to revert to taker fills |
| **Limit timeout** | `ENTRY_LIMIT_TIMEOUT_SEC` | `90` | Cancel unfilled limit entry after N seconds; no market fallback |
| Max Position Size | `MAX_POSITION_PCT` | 20% | Single position capped at 20% of portfolio |
| **Force Close** | `MAX_LOSS_PER_POSITION_PCT` | 20% | Auto-close positions at 20% loss **of MARGIN** (P2.4 — was % of notional). At 10× leverage this triggers on a 2% adverse price move |
| Max Leverage | `MAX_LEVERAGE` | 10× | Hard leverage cap |
| Total Exposure | `MAX_TOTAL_EXPOSURE_PCT` | 80% | All positions combined capped at 80% of portfolio |
| Daily Circuit Breaker | `DAILY_LOSS_CIRCUIT_BREAKER_PCT` | 25% | Stops new trades at 25% daily drawdown |
| Mandatory Stop-Loss | `MANDATORY_SL_PCT` | 5% | Auto-set SL if LLM doesn't provide one (P2.3: rounded to asset-aware price precision, not 2 decimals) |
| Max Positions | `MAX_CONCURRENT_POSITIONS` | 10 | Concurrent position limit |
| Balance Reserve | `MIN_BALANCE_RESERVE_PCT` | 10% | Don't trade below 10% of initial balance |
| **ATR vol thresholds** | `ATR_RATIO_HIGH` / `ATR_RATIO_LOW` | 1.5 / 0.7 | atr_ratio = atr3/atr14 on 4h. Above HIGH → high-vol regime; below LOW → low-vol regime (P2.1) |
| **High-vol size mult** | `LOW_SIZE_MULT` | 0.5 | Allocation × 0.5 when atr_ratio > ATR_RATIO_HIGH (P2.1) |
| **Low-vol size mult** | `HIGH_SIZE_MULT` | 1.0 | Allocation × 1.0 when atr_ratio < ATR_RATIO_LOW — **NO boost above cap** (P2.1) |
| **Min reward:risk** | `MIN_RR` | 1.5 | Reject entries with R:R below this. TP=null bypasses the gate (P2.6) |
| **Min volume conviction** | `MIN_VOL_SPIKE_RATIO` | 0.5 | Block new entries when 5m vol_spike_ratio < 0.5 (dead-tape filter, P2.7) |
| Sharpe sample gate | `MIN_SHARPE_SAMPLE` | 10 | Don't compute Sharpe until this many closed trades exist (P2.5) |
| Sharpe window | `SHARPE_WINDOW` | 50 | Most-recent closed trades used in Sharpe (P2.5) |

## Phase 0 — Day-0 blockers (operational safety)

Make the bot runnable, safe, and observable. See `IMPROVEMENT_PLAN.md` for the full rationale.

| Item | Env var / change | Default | Description |
|------|------------------|---------|-------------|
| **0.1 Model migration** | `LLM_MODEL` | `claude-sonnet-5` | `claude-sonnet-4-20250514` **retired 2026-06-15**. Thinking now uses the current shape: off by default (`{type:disabled}`); `THINKING_ENABLED=true` → adaptive, with optional `THINKING_EFFORT` (`low`..`max`). |
| **0.2 Loopback API** | `API_HOST` | `127.0.0.1` | Status server no longer binds `0.0.0.0` (the signing-key host was being scanned). Use an SSH tunnel for remote access. HTTP access logs go to `api_access.log`. |
| **0.3 Error honesty** | — | — | LLM failures are logged with specific reason codes (`api_error:<class>:<status>`, `empty_response`, `tool_loop_exhausted`) and a machine-readable `error` field in `decisions.jsonl` — no more `"tool loop cap"` mislabel that hid a 69h outage. |
| **0.4 Watchdog** | `ERROR_HOLD_ALERT_AFTER` / `ERROR_HOLD_RESTART_AFTER` / `WATCHDOG_RESTART_WHEN_FLAT` | `3` / `8` / `true` | Pages after N consecutive error-holds (re-pages every N thereafter). At M it `exit(1)`s for a supervisor restart **only when flat** — while positions are open it keeps running so local force-close/SL keep protecting them. A `HEARTBEAT` line is logged every cycle. Counts `api_error`/`empty_response`/`tool_loop_exhausted`/`parse_error`/`agent_exception`. |
| **0.5 Spend telemetry** | `LLM_DAILY_SPEND_ALERT_USD` | `1.00` | Per-cycle LLM cost (`llm_cost_usd`, cumulative) logged to `decisions.jsonl`; alert when a UTC day's spend exceeds the limit. |
| **0.6 Push alerts** | `ALERT_WEBHOOK_URL` | _(unset)_ | Non-blocking operator alerts (ntfy.sh or generic webhook). Unset → alerts are logged only. |
| **0.7 Startup fingerprint** | — | — | Logs git SHA, model, interval, assets, gate flags, network, and boot equity/positions on every start. |
| **0.8 Balance sanity** | `EQUITY_SANITY_DEVIATION_PCT` / `EQUITY_SUSPECT_ADOPT_AFTER` | `50` / `4` | Reads `marginSummary.accountValue` (was always 0) and includes negative PnL in the equity fallback. On a >N% single-cycle equity jump it skips **new entries only** (protective force-close/SL/exit-rules still run); the last *good* equity is kept as baseline (a glitch can't poison sizing), and a reading is adopted as real only after it persists `EQUITY_SUSPECT_ADOPT_AFTER` cycles. |

## Setup

### Prerequisites
- Python 3.12+
- Anthropic API key
- Hyperliquid wallet (agent wallet as signer + main wallet with funds)

### Configuration

```bash
cp .env.example .env
# Edit .env with your keys
```

Required environment variables:
- `ANTHROPIC_API_KEY` — Claude API key
- `HYPERLIQUID_PRIVATE_KEY` — Agent/API wallet private key (signer only)
- `HYPERLIQUID_VAULT_ADDRESS` — Main wallet address (holds funds)
- `ASSETS` — Space-separated list of assets to trade
- `INTERVAL` — Trading loop interval (e.g. `5m`, `1h`)

### Install & Run

```bash
pip install hyperliquid-python-sdk anthropic python-dotenv aiohttp requests
python3 src/main.py
```

Or with CLI args:
```bash
python3 src/main.py --assets "BTC ETH SOL xyz:GOLD xyz:TSLA" --interval 5m
```

### Agent Wallet Setup

1. Go to app.hyperliquid.xyz → Settings → API Wallets
2. Add your agent wallet address as an authorized signer
3. Set `HYPERLIQUID_VAULT_ADDRESS` to your main wallet address in `.env`

The agent wallet signs trades on behalf of your main wallet. It cannot withdraw funds.

## Structure

```
src/
  main.py                  # Entry point, trading loop, API server
  config_loader.py         # Environment config with defaults
  risk_manager.py          # Safety guards (position limits, loss protection)
  agent/
    decision_maker.py      # Claude API integration, tool calling
  indicators/
    local_indicators.py    # EMA, RSI, MACD, ATR, BBands, ADX, OBV, VWAP
    taapi_client.py        # Legacy (unused) — kept for reference
  trading/
    hyperliquid_api.py     # Order execution, candles, state queries
  utils/
    formatting.py          # Number formatting
    prompt_utils.py        # JSON serialization helpers
```

## How It Works

Each loop iteration:
1. Fetches account state (balance, positions, PnL)
2. Force-closes any position at >= 20% loss
3. Gathers candle data and computes indicators for all assets
4. Sends everything to Claude with risk limits
5. Claude returns buy/sell/hold decisions with allocation, TP/SL
6. Risk manager validates each trade (caps allocation, enforces SL)
7. Executes approved trades (market or limit orders)

## API Endpoints

When running, serves a local API:
- `GET /diary` — Recent trade diary entries as JSON
- `GET /logs` — LLM request logs

## Dashboard

A separate Next.js dashboard is available for real-time PnL and trade monitoring. See the `dashboard/` directory or deploy to Vercel.

## License

Use at your own risk. No guarantee of returns. This code has not been audited.
