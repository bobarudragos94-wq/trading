# Santinela — Autonomous Crypto Spot Trading System

Freqtrade execution engine + Claude-powered "Strategist" for slow, high-level
decisions + a locked, deterministic RiskGuard that nothing can bypass.

> **Status: DRY-RUN ONLY.** The v1 baseline strategies showed **negative
> expectancy in every backtested regime** at realistic retail costs (see
> "Measured backtest results"). Per the philosophy below, live trading is
> blocked by the go-live gate until a configuration with a demonstrated,
> walk-forward-validated edge exists AND ≥4 clean weeks of dry-run back it
> up. The infrastructure is complete; the edge is not. That is a finding,
> not a failure.

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

## Measured backtest results (v1 baseline — do not skip this section)

Windows (OKX USDT data — Kraken's public API serves only ~720 candles, so
deep history comes from OKX through the standard `freqtrade download-data`
path; live trading remains Kraken/USDC, same assets):

- **bear** 2022-04-01→2022-11-30, **bull** 2023-10-01→2024-03-31,
  **range** 2024-04-01→2024-09-30
- fees 0.35%/side (Kraken tier-0 maker/taker blend + slippage allowance),
  conservative other-side fills, neutral preset (1.25% risk, 3 slots)

| strategy | window | trades | win% | profit% | PF | maxDD% |
|---|---|---|---|---|---|---|
| TrendRider | bear | 177 | 23.2 | -38.6 | 0.30 | 38.8 |
| TrendRider | bull | 483 | 36.2 | -62.8 | 0.46 | 63.6 |
| TrendRider | range | 138 | 24.6 | -27.4 | 0.22 | 27.4 |
| MeanRevRanger | bear | 365 | 37.8 | -49.2 | 0.35 | 49.9 |
| MeanRevRanger | bull | 257 | 40.9 | -27.7 | 0.51 | 28.4 |
| MeanRevRanger | range | 262 | 35.5 | -34.5 | 0.32 | 35.6 |

Lookahead-analysis: clean for both strategies (0 biased signals).
Recursive-analysis: max indicator variance 0.079% at the configured warmup.

**Honest read:** both v1 strategies lose in every regime. Exit-reason stats
show why: initial 2×ATR stops average ≈−3.5% in ~4h, the 1.5×ATR trail cuts
winners at ≈+1R, the EMA20 exit loses on average, and ~0.7–0.9% round-trip
costs dominate at 1h signal frequency. Candidate directions (validate with
`make hyperopt` walk-forward before believing anything): slower signals
(4h), wider trailing, stricter entry filters to cut trade count, maker-only
exits. Until a config wins out-of-sample across regimes, the go-live gate
stays shut.

## Go-live procedure

`make live` runs `scripts/go_live_gate.py`, which refuses unless ALL of:

1. Full `pytest` suite green.
2. `TRADING_MODE=live` in `.env`.
3. `user_data/config.live.json` exists, valid, spot-only, `dry_run=false`,
   USDC stake, no secrets inside, `force_entry_enable=false`.
4. Secrets configured (exchange key/secret; strong `FT_API_PASSWORD`).
5. **S9**: the exchange key is verified **trade-only** — a withdrawal-info
   probe must be DENIED by the exchange. Keys with withdrawal rights are
   refused, here and again at every live boot.
6. **Dry-run record** (§7.3): ≥28 days span, ≥30 closed trades, profit
   factor ≥1.15, max drawdown ≤12% — measured from the dry-run database,
   never self-reported.
7. Journal present (S12).
8. You type exactly `INTELEG RISCUL` at the prompt (interactive TTY only).

Then — and only then — the live compose stack starts. Start with money you
can afford to lose entirely.

## Runbook

### Daily operation (dry-run or live)
- `make logs` tails both services; FreqUI at http://127.0.0.1:8080.
- The brain journals every decision to `journal/*.jsonl` (append-only) and
  writes a daily report to `reports/YYYY-MM-DD.md` at 07:00
  (Europe/Bucharest), with a Telegram summary when configured.
- Telegram: freqtrade bot answers `/status /profit /daily /stopentry /stop`;
  the Santinela bot (second token) answers `/guard /verdict /panic`.

### Panic procedure
1. Send `/panic` to the Santinela bot → it arms and asks for confirmation.
2. Reply exactly `CONFIRM PANIC` within 5 minutes → entries stop and every
   open position is market-exited. Any other reply disarms.
3. Without Telegram: `docker compose exec freqtrade freqtrade stop` or
   `make stop` (stops containers; open positions keep exchange-side stops
   only if `stoploss_on_exchange` is enabled — live config enables it).

### After an S7 kill switch
1. The bot is stopped, `user_data/riskguard_state/KILLED` exists, Telegram
   alerted. Investigate first — the journal has the full decision trail.
2. Resume ONLY deliberately: remove the `KILLED` file manually, set
   `ACKNOWLEDGE_DRAWDOWN=1` in `.env`, restart (`make dryrun`/`make live`).
   Both steps are required; the high-water mark re-bases on resume. Unset
   `ACKNOWLEDGE_DRAWDOWN` afterwards.

### Updating
- `git pull`, then `make test` (must be green), then `make dryrun` —
  compose rebuilds the brain image and restarts pinned containers.
- Never edit risk limits at runtime; they live in `riskguard/guard.py` and
  require code review + redeploy by design.

### Backups
- Back up `user_data/*.sqlite` (trade DBs), `journal/`,
  `user_data/riskguard_state/` and your `.env` (offline). Everything else
  is reproducible from git.

### Windows notes
- Requires Docker Desktop (WSL2 backend) and GNU make (via Git Bash, WSL,
  or `choco install make`). Run everything from the repo root.
- Laptop must not sleep: set power plan accordingly; Docker Desktop
  "Start at login" + compose `restart: unless-stopped` handle reboots.

### Data & research
- `make data` downloads OKX history for backtesting; `make backtest` runs
  the 3-window suite + lookahead/recursive analysis;
  `ENABLE_PROTECTIONS=1 make backtest` adds freqtrade-level protections.
- `make hyperopt` (bounded spaces, walk-forward) — reject any params that
  only win in one regime.

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
