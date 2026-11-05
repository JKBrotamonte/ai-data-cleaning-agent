"""Human approval for risky steps: a y/n prompt in the terminal.

A plan with steps that delete or change the meaning of data can't be applied until a
person says yes. The yes is stored as a fingerprint of the exact plan, so any change to
the plan (a re-plan after a QA failure, say) needs a new yes.
"""

from cleaner_agent import common


def plan_fingerprint(plan: dict) -> str:
    return common.fingerprint(plan.get("plan"))


def is_approved(plan: dict) -> bool:
    return not plan.get("needs_approval") or plan.get("approved") == plan_fingerprint(plan)


def ask(plan: dict, assume_yes: bool = False, prompt=None) -> bool:
    """Show the risky steps and ask once. assume_yes is for unattended benchmark runs only."""
    if is_approved(plan):
        return True
    print("These steps delete or change the meaning of data:")
    for r in plan["needs_approval"]:
        print(f"  step {r['step']} ({r['op']}): {r['reason']}")
    if assume_yes:
        print("Approved automatically (--yes).")
        return True
    try:
        answer = (prompt or input)("Run this plan? [y/N] ")
    except EOFError:  # no terminal attached: never approve by default
        answer = ""
    return answer.strip().lower() in ("y", "yes")
