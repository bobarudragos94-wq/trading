# Santinela — Autonomous Crypto Spot Trading System

Freqtrade execution engine + Claude-powered "Strategist" for slow, high-level
decisions + a locked, deterministic RiskGuard that nothing can bypass.

> **Status: under construction, milestone by milestone. Dry-run only.**

---

## ⚠️ Risk disclaimer

Trading cryptocurrencies involves substantial risk of loss. **You can lose
everything you deposit.** This software is provided as-is, with no warranty and
no performance claims. Nothing here is financial advice. Only trade money you
can afford to lose entirely. Past backtest results do not predict future
returns.

## Philosophy — capital preservation first

- The system has **no proven edge** until it demonstrates one across a full
  backtest suite **and** at least 4 weeks of dry-run.
- The target is **positive expectancy with controlled drawdown** — not a daily
  profit quota. No trades is a perfectly good outcome for a day, a week, or a
  month.
- Any component that cannot prove it adds value gets removed.
- The LLM is **advisory only**. It nudges regime/risk posture within hard
  bounds; it can never place an order, raise a limit, or disable a stoploss.
- Going live is a deliberate, multi-step, manual action. Dry-run is the
  default, and the default is sticky on purpose.

## Non-negotiable safety rules (locked surfaces)

These are enforced in code (`riskguard/`), unit-tested, and cannot be changed
by the LLM, by Telegram commands, or by hot config reloads. Changing them
requires editing code and redeploying — a human action.

| #   | Rule                                    | Value |
|-----|-----------------------------------------|-------|
| S1  | Spot only — no margin/futures/leverage/shorts | validated at boot |
| S2  | Max risk per trade (stop distance × size) | 2.0% of equity |
| S3  | Max concurrent open trades              | 4 |
| S4  | Max total exposure                      | 60% of equity |
| S5  | Hard stoploss on every position         | mandatory; may tighten, never remove |
| S6  | Daily loss circuit breaker              | −5% in rolling 24h → pause entries 24h |
| S7  | Max drawdown kill switch                | −20% from HWM → full stop, manual restart |
| S8  | Consecutive-loss brake                  | 6 losses in a row → pause entries 12h |
| S9  | Exchange API keys                       | trade-only, withdrawals disabled, IP-whitelisted; verified at boot |
| S10 | LLM output is advisory                  | validated + clamped; outage → `defensive` preset |
| S11 | Live mode gate                          | env + separate config + typed confirmation |
| S12 | Append-only audit                       | every decision journaled to JSONL |

## Architecture

```
                         ┌─────────────────────────────┐
                         │  LLM Strategist (Python svc)│
   OHLCV summaries ────▶ │  Claude API, every 4h +     │
   vol/funding stats     │  daily report at 07:00 EET  │
                         └──────────┬──────────────────┘
                                    │ verdict.json (bounded)
                                    ▼
┌──────────────┐  validate  ┌─────────────────┐   REST/API   ┌────────────────┐
│ RiskGuard    │◀───────────│ Orchestrator    │─────────────▶│ Freqtrade      │
│ (pure python,│  approve/  │ (applies preset,│              │ (strategy,     │
│  locked)     │  clamp     │  schedules jobs)│              │  orders, DB)   │
└──────┬───────┘            └──────┬──────────┘              └───────┬────────┘
       │ trip breakers             │                                 │ CCXT
       ▼                           ▼                                 ▼
   Telegram alerts          journal/*.jsonl                  Exchange (Kraken
   + /status /panic         (append-only)                    default; MiCA-licensed)
```

- **Freqtrade** owns execution: entries/exits, stoplosses, order retries,
  SQLite persistence, backtesting, dry-run, Telegram, FreqUI.
- **Strategist** never talks to the exchange. It reads compact market
  summaries and returns one bounded JSON verdict every 4 hours.
- **Orchestrator** maps the verdict onto Freqtrade config, inside RiskGuard
  bounds, via the Freqtrade REST API.
- **RiskGuard** is a small, pure-Python, fully unit-tested module — the locked
  surface. It clamps every parameter, monitors equity, and trips breakers
  S6–S8.

## Exchange & regulation note

Default exchange is **Kraken** (MiCA-licensed for the EU). The CCXT id is
configurable (`kraken`, `okx`, `bybit`), but **before going live you must
verify yourself** that your chosen exchange is MiCA-authorised for your
country in the [ESMA CASP register](https://www.esma.europa.eu/publications-and-data/registers-and-data).

## Quick start (dry-run)

```bash
make setup      # creates .env from the example — fill in your values
make test       # full safety-rule test suite; must be green
make dryrun     # starts freqtrade + brain in paper-trading mode
make logs       # watch it
```

FreqUI: http://127.0.0.1:8080 (credentials from `.env`).

## Go-live procedure

Documented in full once M7 lands. In short: 3 backtest regimes clean +
lookahead/recursive analysis clean + ≥4 weeks dry-run (≥30 trades, profit
factor ≥1.15, max DD ≤12%, zero unhandled exceptions) + trade-only API keys
verified + `TRADING_MODE=live` + `config.live.json` + typing the confirmation
phrase at the gate. Each step is enforced by `make live`, not by good
intentions.

## Repository layout

```
docker-compose.yml        # dry-run stack (freqtrade + brain), pinned images
docker-compose.live.yml   # live override, only reachable through `make live`
Makefile                  # setup / test / backtest / hyperopt / dryrun / live
user_data/                # freqtrade configs + strategies
strategist/               # Claude client, prompts, verdict schema, scheduler
riskguard/                # LOCKED limits S1–S12, breaker state machine
orchestrator/             # verdict -> freqtrade REST, always through riskguard
journal/                  # append-only JSONL audit (git-ignored)
reports/                  # daily Markdown reports (git-ignored)
tests/                    # riskguard tests are the most important in the repo
scripts/                  # backtest suite, key-permission verifier, go-live gate
```

## Out of scope for v1 (deliberately)

Leverage/futures/shorting; news or X-sentiment ingestion; FreqAI/ML; grid,
DCA, martingale; multi-exchange arbitrage; auto-modifying risk limits;
auto-deposit/withdrawal (never); dashboards beyond FreqUI.
