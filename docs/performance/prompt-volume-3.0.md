# 3.0 Prompt Volume Baseline

The deterministic prompt gate compares the fixed text of four core AI flows
with the tagged `v2.9.0` implementation. The baseline was measured by rendering
the 2.9 workspace, chapter, and merged-cataloging prompts and by counting the
fixed new-book stage contract at its largest stage.

Run `backend/.venv/Scripts/python.exe backend/scripts/check_prompt_budget.py`
from the repository root. CI fails if the combined reduction falls below 20%.
The 3.0 RC baseline is 3,794 characters versus 30,529 in 2.9, an 87.57%
reduction, and every individual flow clears the 20% threshold. Runtime context and author-authored prompt overrides are excluded
because they are task data rather than fixed controller instructions.
