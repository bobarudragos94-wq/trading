"""RiskGuard — the locked safety surface of Santinela (rules S1-S12).

Pure Python, stdlib only, zero network calls. Imported by the freqtrade
strategies, the orchestrator and the strategist scheduler. Nothing in this
package may ever be relaxed at runtime: changing a limit requires editing
code and redeploying.
"""

from riskguard.guard import (  # noqa: F401
    LOCKED,
    PRESETS,
    BreakerStatus,
    Decision,
    can_open_new_trade,
    check_breakers,
    clamp_verdict,
    position_stake,
    record_equity,
    record_trade_result,
    validate_boot_config,
)
from riskguard.state import GuardState, load_state, save_state  # noqa: F401
