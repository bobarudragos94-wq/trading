# HANDOFF — continuation instructions for the next session

> Purpose: let any future Claude session (or a human) resume the Santinela
> build exactly where it stopped. Keep this file updated at every milestone
> commit. Communicate with the user in Romanian; write all code, comments,
> commits and docs in English.

## What this project is

Autonomous crypto **spot** trading system per the build spec in the original
task ("Santinela"): Freqtrade execution engine + Claude LLM Strategist
(advisory only) + locked deterministic RiskGuard (rules S1–S12). Dry-run by
default; going live is a gated, multi-step manual action. Philosophy:
capital preservation first; no component keeps its place without proving
value. Full spec: see the original task message; the S1–S12 table is in
README.md.

## Milestone status (spec §11)

| Milestone | Status | Notes |
|---|---|---|
| M0 scaffold | ✅ committed | compose validated, `make test` green |
| M1 RiskGuard | ✅ committed | 100% stmt+branch coverage on riskguard/ |
| M2 dry-run boot | ✅ committed | verified live against Kraken in sandbox: 25-pair whitelist, REST /status, forced dry trade w/ ATR stop, hot reload |
| M3 strategies+backtests | ✅ committed | table + honest read below; lookahead clean; recursive ≤0.08% at startup 499 |
| M4 strategist+orchestrator | ✅ committed | fake verdicts (valid/malicious/invalid) demoed end-to-end against live bot; REAL Claude call still pending user's ANTHROPIC_API_KEY (fallback path journaled instead) |
| M5 breaker e2e demo | ⬜ | plan below |
| M6 daily report + TG cmds | ⬜ | plan below |
| M7 go-live gate + runbook | ⬜ | plan below |

## Environment facts (sandbox-specific, re-verify if resuming elsewhere)

- Repo root = `/home/user/trading`, branch `claude/santinela-crypto-trading-jdhb93`
  (push with `git push -u origin <branch>`; never another branch).
- Local pip has freqtrade==2026.6, anthropic==0.116.0, pydantic==2.13.4,
  talib 0.6.8; CLI at `/root/.local/bin/freqtrade`; always `PYTHONPATH=.`.
- Outbound HTTPS goes through a MITM proxy (`$HTTPS_PROXY`, e.g.
  `http://127.0.0.1:35685`). For freqtrade/ccxt set BOTH env overrides:
  `FREQTRADE__EXCHANGE__CCXT_CONFIG='{"httpsProxy": "<proxy>", "enableRateLimit": true}'`
  `FREQTRADE__EXCHANGE__CCXT_ASYNC_CONFIG='{"httpsProxy": "<proxy>", "aiohttpTrustEnv": true, "enableRateLimit": true}'`
  and append `/root/.ccr/ca-bundle.crt` to certifi's `cacert.pem` (already
  done for local python; for docker, mount a merged bundle over
  `/home/ftuser/.local/lib/python3.14/site-packages/certifi/cacert.pem`).
- Docker daemon: start with `dockerd --iptables=false --bridge=none &`;
  containers need `--network host` to reach the proxy. Image
  `freqtradeorg/freqtrade:2026.6` is pulled. Run containers as default user
  (ftuser), NOT `-u 0`; `chmod -R a+rwX user_data journal` first.
- A test container `santinela-m2` may exist (freqtrade dry-run on Kraken,
  API on 127.0.0.1:8080, user `santinela`, password `m2-test-password-longer`).
  Recreate per the M2 commit message commands if gone.
- api.binance.com is geo-blocked (451); OKX API works → backtest data source
  is OKX USDT pairs in `user_data/data/okx/` (8 majors, 1h/4h/1d,
  2022-01-01→2024-10-09, already downloaded).
- `ANTHROPIC_API_KEY` is NOT available in the sandbox → the real LLM call
  (M4 acceptance) can only be smoke-tested by the user later; test the
  fallback path instead and document.
- Kraken public API works through the proxy. No Telegram token available.

## M3 state (finish first)

Done:
- Strategies `TrendRider` + `MeanRevRanger` + `shared/santinela_base.py`
  (riskguard-gated entries/sizing) — committed in M2, refined since.
- `scripts/backtest_suite.sh` (3 windows: bear 20220401-20221130, bull
  20231001-20240331, range 20240401-20240930; FEE=0.0035/side;
  `--enable-protections` opt-in via env), `scripts/summarize_backtests.py`,
  `scripts/download_data.sh`, `scripts/hyperopt_suite.sh` (walk-forward),
  smoke tests `tests/test_strategies_smoke.py`.
- Lookahead-analysis: CLEAN for both strategies (has_bias: No).
- Recursive-analysis: found ema200_4h variance 1.056% at startup 250 →
  raised `startup_candle_count` to 499 (variance 0.079%). OKX rejects
  startup ≥1499 in that check, hence `--startup-candle 199 499 999`.
- Fixed `_atr_at` to always be time-bounded (defensive vs lookahead).

Last action: backtest suite re-running with startup 499 (log:
`scratchpad/backtest-suite4.log`). Results with startup 250 were, for the
record (fee 0.35%/side, conservative "other"-side fills, neutral preset
1.25%/3 trades, no protections):

| strategy | window | trades | win% | profit% | PF | maxDD% |
|---|---|---|---|---|---|---|
| TrendRider | bear | 177 | 23 | -38.6 | 0.30 | 38.8 |
| TrendRider | bull | 491 | 36 | -63.6 | 0.45 | 63.8 |
| TrendRider | range | 138 | 25 | -26.7 | 0.23 | 26.7 |
| MeanRevRanger | bear | 365 | 38 | -49.2 | 0.35 | 49.9 |
| MeanRevRanger | bull | 257 | 41 | -27.7 | 0.51 | 28.4 |
| MeanRevRanger | range | 262 | 36 | -34.5 | 0.32 | 35.6 |

Honest read (report this to the user at M3 close): both v1 baseline
strategies have **negative expectancy in every regime** at realistic
retail Kraken costs. Diagnosis from exit-reason stats: initial 2×ATR stops
lose ~-3.5% avg in ~4h; the 1.5×ATR trail cuts winners at ~+1R; the EMA20
exit loses on average; ~0.7–0.9% round-trip cost dominates at 1h signal
frequency. This is exactly the "no proven edge" case the README
philosophy anticipates — the go-live checklist must therefore block live
until a config with a real edge exists (bounded hyperopt + walk-forward,
or fewer/slower signals). DO NOT curve-fit to make the table look good;
report measured numbers only.

To finish M3:
1. Wait for suite4; regenerate table (`PYTHONPATH=. python3
   scripts/summarize_backtests.py`); confirm lookahead still clean and
   recursive variance ≤0.1% at 499.
2. Optionally add one `ENABLE_PROTECTIONS=1` bear run for the
   defense-in-depth comparison.
3. `make test` green → commit M3 → push → report table + honest read to
   the user (in Romanian).

## M4 state (DONE — notes for follow-up)

All items below are implemented, tested (36 tests) and demoed against the
live dry-run bot: valid verdict applied (static whitelist + preset visible
in /show_config after reload), malicious verdict clamped (100%→2%, 50→4
trades, evil pairs dropped, whitelist truncated to 8), invalid output →
defensive fallback keeping current strategy; everything journaled (S12).
Gotcha fixed: overrides.json must be chmod 0644 (mkstemp gives 0600 and the
container's ftuser can't read it → freqtrade reload dies).
Remaining for the user: set ANTHROPIC_API_KEY and run one verdict cycle to
log the first REAL Claude call (`llm_call` event) — the no-key path
(`llm_call_skipped` → defensive) is already journaled and demoed.

## M4 original plan (kept for reference)

Written (untested):
- `strategist/journal.py` — append-only JSONL (S12), `append()` + `tail()`.
- `strategist/verdict_schema.py` — pydantic `Verdict`, `parse_verdict()`
  (never raises), `VERDICT_JSON_SCHEMA` for structured outputs.
- `strategist/llm_client.py` — anthropic SDK, model env `STRATEGIST_MODEL`
  (default claude-sonnet-4-6), 60s timeout, exactly 1 manual retry
  (SDK max_retries=0), `output_config={"format": {"type": "json_schema",
  "schema": VERDICT_JSON_SCHEMA}}`, journals tokens/latency/cost;
  returns None on failure → defensive.
- `strategist/prompts/strategist_system.md` — advisory-only system prompt.
- `orchestrator/freqtrade_api.py` — REST client (basic auth via
  FT_API_USERNAME/FT_API_PASSWORD, FT_API_URL).
- `orchestrator/presets.py` — re-exports riskguard PRESETS;
  `freqtrade_overrides(clamped, current_strategy)` builds overrides.json
  content (keeps current strategy when flat; StaticPairList when whitelist).
- `orchestrator/apply_verdict.py` — parse → clamp (with breakers +
  current_strategy) → atomic overrides.json write → /reload_config →
  /stopentry when flat; journals verdict/verdict_applied/verdict_rejected;
  `DEFENSIVE_FALLBACK` on None/invalid.

Still to do for M4:
1. `strategist/market_snapshot.py` — compact (<4k tokens) JSON: per-pair
   1h/4h/1d stats (returns, ATR%, ADX, EMA relations, volume z-score) from
   freqtrade `/pair_candles` (talib available), BTC relative strength (use
   BTC 7d return minus alt-basket mean as dominance proxy — no external
   feeds in v1, leave documented extension point), realized vol, open
   positions, equity, breaker states, last 5 verdicts+outcomes via
   `journal.tail("verdict")`.
2. `strategist/main.py` — scheduler loop: heartbeat file
   `journal/heartbeat` each iteration (compose healthcheck expects it);
   every 5 min equity poll → `record_equity` → persist state → alert+
   journal breaker events (S6/S7: on S7 write kill file + call /stop);
   verdict cycle every 4h at candle close (00/04/08/12/16/20 UTC);
   daily report 07:00 Europe/Bucharest (stub until M6). Boot: refuse if
   KILLED file present unless `ACKNOWLEDGE_DRAWDOWN=1` + file removed
   (riskguard.state.try_resume_after_kill); validate /show_config with
   riskguard.validate_boot_config (S1).
3. `strategist/telegram_alerts.py` — sendMessage via requests when
   TELEGRAM_ENABLED (no getUpdates here — freqtrade owns polling; for M6
   custom commands use a SECOND bot token, env `SANTINELA_TELEGRAM_TOKEN`).
4. Tests: `tests/test_verdict_validation.py` (malformed JSON, out-of-bounds,
   injection rationale, oversized whitelist → clamp/defensive, never crash),
   `tests/test_presets_within_bounds.py`, `tests/test_apply_verdict.py`
   (mock FreqtradeAPI; valid/invalid/malicious verdicts per spec M4
   acceptance; flat → stopentry called; file written atomically).
5. Demo against the running `santinela-m2` container: feed a fake valid
   verdict + an invalid one + a malicious one through
   `apply_verdict.apply_raw_verdict`, show overrides.json + journal lines +
   whitelist change after reload. (Real Claude call: only if user provides
   ANTHROPIC_API_KEY; otherwise document.)
6. Commit M4 + push + report.

## M5 plan (breakers e2e)

- Use the dry-run container + `riskguard` state dir `user_data/riskguard_state/`.
- S6: seed GuardState with equity samples, then `record_equity` with -5%;
  verify strategist loop journals S6_TRIPPED, telegram alert (log if no
  token), confirm_trade_entry refuses (try /forceenter → rejected), pause
  expires after 24h (simulate with injected `now`).
- S7: drop -20% → KILLED file + /stop called; restart brain → refuses to
  trade until file removed AND ACKNOWLEDGE_DRAWDOWN=1; HWM re-based.
- S8: 6 fake losing closed trades via `record_trade_result` → 12h pause.
- Also demonstrate freqtrade-level protections as second net (optional).
- Most of this is already unit-tested in test_riskguard.py; M5 is the
  END-TO-END demo wiring through main.py + the live API.

## M6 plan

- `strategist/daily_report.py`: 07:00 EET Markdown to reports/YYYY-MM-DD.md
  (+ Telegram summary): equity, PnL day/week, open trades, win rate, profit
  factor, max DD, breaker events, verdict history vs outcomes, one
  improvement hypothesis (advisory only). Data from /profit, /status,
  journal. `make report` target exists.
- Custom TG commands via second bot token (SANTINELA_TELEGRAM_TOKEN, long
  polling in brain): /guard (breaker states), /verdict (last raw+clamped),
  /panic (= /stopentry, then market-exit all after explicit "CONFIRM
  PANIC" reply within timeout). Restrict to TELEGRAM_CHAT_ID.

## M7 plan

- `scripts/verify_api_permissions.py`: Kraken API — call a private
  read endpoint + attempt withdrawal-permission introspection; refuse keys
  with withdraw rights (S9). Kraken: use `/0/private/Balance` ok +
  check key permissions via error behavior of Withdraw endpoints WITHOUT
  executing (query `WithdrawMethods`? choose safest: attempt
  `/0/private/WithdrawInfo` with dummy params and expect
  'EGeneral:Permission denied' for a compliant key).
- `scripts/go_live_gate.py`: interactive; checks pytest green, TRADING_MODE
  =live, config.live.json exists+valid+spot, dry-run stats thresholds from
  journal/db (≥4 weeks, ≥30 trades, PF≥1.15, maxDD≤12%), key permission
  script passes, prints S1–S12 table, requires typing `INTELEG RISCUL`.
- README runbook: full go-live checklist, ops (backup, restart, updates),
  Windows notes (Docker Desktop, WSL2), MiCA disclaimer, panic procedure.
- `make live` already wired to gate + docker-compose.live.yml.

## Conventions & gotchas

- Commit style: `M<N>: <summary>` + bullet body; end with the
  Co-Authored-By + Claude-Session trailer (see `git log`).
- Never touch values in riskguard without user approval — it is the locked
  surface (S1–S12); presets live in riskguard.guard.PRESETS.
- Strategy switching = orchestrator writes overrides.json + /reload_config
  (freqtrade re-creates the bot incl. strategy from config `strategy` key;
  CLI never passes --strategy).
- `stake_amount: "unlimited"` + `custom_stake_amount` does the sizing;
  never remove the hard `stoploss = -0.10` (S5).
- Freqtrade env config overrides: `FREQTRADE__SECTION__KEY` (values parsed
  as JSON when possible). Secrets only via env, never in JSON.
- Journal is append-only; never rewrite (S12).
- The 2 sandbox proxy env overrides above must NOT leak into committed
  files — compose/live config stay clean for the user's Windows laptop.
- User's stack: Windows laptop + Docker Desktop; document accordingly.
