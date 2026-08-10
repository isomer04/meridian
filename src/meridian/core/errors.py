"""Six error classes — `MeridianError` and the five that specialise it — and the
distinction between two of them is the whole point.

`VendorError` is handed back to the agent as a string. A bureau timeout is worth
reasoning about — retry, try the other bureau, note it as a condition.

`PolicyViolation` propagates. It is not caught, not stringified, not returned to
the model. If a refusal comes back as text the agent simply rephrases, and "an
agent that cannot do it" degrades into "an agent that was told no once". That is
the difference between a guardrail and a control.
"""


class MeridianError(Exception):
    """Base for everything raised inside meridian."""


class VendorError(MeridianError):
    """A downstream vendor failed. Recoverable; the agent may adapt.

    Tool wrappers catch this and return the message as tool output so the agent
    can reason about it.
    """

    def __init__(self, vendor: str, message: str, retryable: bool = True):
        self.vendor = vendor
        self.retryable = retryable
        super().__init__(f"[{vendor}] {message}")


class PolicyViolation(MeridianError):
    """A precondition required by law or policy was not met.

    Never returned to an agent as text. Aborts the task.
    """

    def __init__(self, control: str, message: str, citation: str | None = None):
        self.control = control
        self.citation = citation
        detail = f" ({citation})" if citation else ""
        super().__init__(f"POLICY[{control}]: {message}{detail}")


class InDoubt(MeridianError):
    """A side effect was reserved, the process did not live to record its response, and
    nobody can say from the ledger whether the vendor was actually called.

    The honest answer to "did the credit pull happen?" here is *we do not know*, and the
    only safe automatic behaviour is to refuse rather than guess. Guessing "no" fires a
    second hard inquiry; guessing "yes" fabricates a response. Clearing it is an operator
    decision — confirm with the vendor, then either record the response or bump the
    attempt epoch to authorise a fresh call.
    """

    def __init__(self, step_name: str, loan_id: str, key: str):
        self.step_name = step_name
        self.loan_id = loan_id
        self.key = key
        super().__init__(
            f"IN DOUBT[{step_name}]: {loan_id} has a reserved but unfilled idempotency key "
            f"{key[:12]} — a prior attempt may already have fired this side effect"
        )


class ProcessKilled(MeridianError):
    """The simulated mid-flow process death of scenario 5.

    A real `SystemExit` derives from `BaseException` and so is not caught by the harness's
    `except Exception`, which turns a scenario *about* recovering from a crash into an
    actual crash of the runner. This is an ordinary exception a caller can record.
    """


class ApprovalRequired(MeridianError):
    """The flow is durably paused at a human approval gate."""

    def __init__(self, gate: str, loan_id: str):
        self.gate = gate
        self.loan_id = loan_id
        # CrewAI's Flow runtime checks this marker before logging listener exceptions.
        # A durable human pause is expected control flow and is rendered by each caller;
        # presenting it as an execution error obscures real failures in the console.
        self._flow_listener_logged = True
        super().__init__(f"APPROVAL REQUIRED[{gate}]: {loan_id} is waiting in the human queue")


class CompensationError(MeridianError):
    """A compensating action itself failed.

    Kept as a distinct class so the saga can report it honestly rather than
    swallowing it. In this scope it surfaces to the operator; a production system
    would park the loan in a COMPENSATION_FAILED state (see roadmap).
    """
