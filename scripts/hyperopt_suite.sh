#!/usr/bin/env bash
# Bounded hyperopt with walk-forward validation (spec §7.2).
#
# Every tunable parameter already carries a bounded space in the strategy
# files — hyperopt cannot explore outside those bounds. This script trains
# on one window and validates on a later, unseen window; a parameter set
# that only wins in one regime must be rejected (see README).
#
# Usage: STRATEGY=TrendRider EPOCHS=100 bash scripts/hyperopt_suite.sh
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH="$PWD"

CONFIG=user_data/config.backtest.json
STRATEGY="${STRATEGY:-TrendRider}"
EPOCHS="${EPOCHS:-100}"
FEE="${FEE:-0.0035}"

TRAIN_RANGE="${TRAIN_RANGE:-20220401-20231001}"   # bear + recovery
TEST_RANGE="${TEST_RANGE:-20231001-20240930}"     # unseen bull + range

echo "=== hyperopt ${STRATEGY}: train ${TRAIN_RANGE} (${EPOCHS} epochs) ==="
freqtrade hyperopt \
    --config "$CONFIG" \
    --strategy "$STRATEGY" \
    --hyperopt-loss SharpeHyperOptLoss \
    --spaces buy sell \
    --timerange "$TRAIN_RANGE" \
    --fee "$FEE" \
    -e "$EPOCHS"

echo "=== walk-forward validation on unseen ${TEST_RANGE} ==="
freqtrade backtesting \
    --config "$CONFIG" \
    --strategy "$STRATEGY" \
    --timerange "$TEST_RANGE" \
    --fee "$FEE" \
    --cache none

cat <<'NOTE'
NOTE: compare the validation metrics against the un-tuned baseline from
scripts/backtest_suite.sh. Accept the tuned parameters ONLY if they hold up
on the unseen window across regimes (profit factor and drawdown at least as
good). Otherwise discard them — a config that only wins in one regime is
overfit. Tuned params land in user_data/strategies/*.json; delete that file
to revert to defaults.
NOTE
