"""Cross-domain approval orchestration facade.

The current persistence implementation remains callable through the repository
while callers migrate to this explicit service boundary.
"""

from offerpilot.approvals.repository import (
    cancel_open_approvals,
    claim_approval,
    decide_approval,
    finish_approval,
)

__all__ = ["cancel_open_approvals", "claim_approval", "decide_approval", "finish_approval"]
