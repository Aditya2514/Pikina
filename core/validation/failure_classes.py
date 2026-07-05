"""
Failure classification — replaces binary valid/rejected with typed outcomes.
Every failure gets a class so the system knows exactly what to do next.
"""
from enum import Enum


class FailureClass(str, Enum):
    VALIDATION     = "validation"      # Bad params / unknown tool -> ask clarifying question
    PERMISSION     = "permission"      # Tier threshold crossed    -> wait for explicit confirm
    RECOVERABLE    = "recoverable"     # Transient (file lock, timeout) -> retry capped
    INFRASTRUCTURE = "infrastructure"  # Ollama down, DB unreachable   -> hand off to recovery
    UNCLASSIFIED   = "unclassified"   # Anything else -> stop, log full context, surface to user


RESPONSE_POLICY: dict = {
    FailureClass.VALIDATION:     "ask_clarifying_question",
    FailureClass.PERMISSION:     "wait_for_explicit_confirmation",
    FailureClass.RECOVERABLE:    "retry_capped",
    FailureClass.INFRASTRUCTURE: "hand_off_to_recovery",
    FailureClass.UNCLASSIFIED:   "stop_and_surface",
}


def policy_for(fc: FailureClass) -> str:
    return RESPONSE_POLICY.get(fc, RESPONSE_POLICY[FailureClass.UNCLASSIFIED])
