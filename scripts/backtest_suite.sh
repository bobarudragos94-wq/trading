#!/usr/bin/env bash
# Multi-regime backtest suite (spec §7.1).
#
# Runs both strategies over three distinct historical windows (bear, bull,
# sideways), fees + slippage modelled via --fee, then runs freqtrade's
# lookahead-analysis and recursive-analysis, which must come back clean.
#
# Windows (chosen from available OKX data, documented in README):
#   bear : 2022-04-01 -> 2022-11-30  (BTC ~45k -> ~17k; LUNA/FTX collapses)
#   bull : 2023-10-01 -> 2024-03-31  (BTC ~27k -> ~71k)
#   range: 2024-04-01 -> 2024-09-30  (BTC chopping ~54k-72k, no trend)
#
# FEE=0.0035 per side ~= Kraken spot maker/taker (0.25-0.40% at small
# volume) plus a slippage allowance. Override with FEE=... if needed.
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH="$PWD"

CONFIG=user_data/config.backtest.json
FEE="${FEE:-0.0035}"
RESULTS=user_data/backtest_results
rm -rf "$RESULTS" && mkdir -p "$RESULTS"

declare -A WINDOWS=(
    [bear]=20220401-20221130
    [bull]=20231001-20240331
    [range]=20240401-20240930
)

for name in bear bull range; do
    range="${WINDOWS[$name]}"
    for strat in TrendRider MeanRevRanger; do
        echo "=== backtest: ${strat} / ${name} (${range}) fee=${FEE} ==="
        freqtrade backtesting \
            --config "$CONFIG" \
            --strategy "$strat" \
            --timerange "$range" \
            --fee "$FEE" \
            --export trades \
            --cache none \
            ${ENABLE_PROTECTIONS:+--enable-protections}
    done
done

echo "=== summary table ==="
python3 scripts/summarize_backtests.py "$RESULTS"

echo "=== lookahead-analysis (must be clean) ==="
for strat in TrendRider MeanRevRanger; do
    freqtrade lookahead-analysis \
        --config "$CONFIG" \
        --strategy "$strat" \
        --timerange "${WINDOWS[range]}" \
        --fee "$FEE"
done

echo "=== recursive-analysis (variance must be ~0) ==="
# Startup values are capped at 999: freqtrade rejects startup >= 5x the
# exchange's per-request candle limit (OKX: 300 -> cap 1499) regardless of
# local data. 250 is the strategies' real startup_candle_count.
for strat in TrendRider MeanRevRanger; do
    freqtrade recursive-analysis \
        --config "$CONFIG" \
        --strategy "$strat" \
        --pairs BTC/USDT \
        --timerange 20230601-20240901 \
        --startup-candle 199 499 999
done
