#!/usr/bin/env bash
# Download OHLCV history for the backtest suite.
#
# Data source: OKX USDT spot pairs. Kraken's public OHLC API only serves the
# most recent ~720 candles per timeframe, which cannot feed multi-year
# backtests; OKX serves deep history through the normal freqtrade
# download-data path. Live trading remains on Kraken/USDC — same assets.
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH="$PWD"

CONFIG=user_data/config.backtest.json
TIMERANGE="${TIMERANGE:-20220101-20241001}"

freqtrade download-data \
    --config "$CONFIG" \
    -t 1h 4h 1d \
    --timerange "$TIMERANGE" \
    "$@"
