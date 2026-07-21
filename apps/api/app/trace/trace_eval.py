"""Compatibility re-export layer for trace and eval.

New code should import directly from:
  - app.trace.trace  (trace CRUD)
  - app.eval.eval    (eval system)
"""

from app.trace.trace import (
    create_trace,
    add_trace_event,
    complete_trace,
    get_trace,
)
from app.eval.eval import (
    EVAL_CASES,
    run_eval,
    run_all_evals,
    save_eval_run,
)
