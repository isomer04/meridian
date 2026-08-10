"""Pure-python reliability layer. ZERO CrewAI imports, by design.

Build order equals risk order: everything in `core/`, `calc/`, `vendors/` and `rag/`
runs on stdlib + numpy + openai and is testable and demoable without the agent
framework installed. If CrewAI fights the install, there is still a working system.
"""

from .config import chat_key_present, embeddings_available, load_env, model_name
from .db import Database, get_db, reset_db, set_db
from .errors import CompensationError, MeridianError, PolicyViolation, VendorError
from .events import BUS, Event, EventBus, emit
from .idempotency import idempotent, make_key
from .policy import PolicyContext, assert_no_prohibited_basis, requires_state
from .saga import Saga, SagaStep

__all__ = [
    "load_env",
    "model_name",
    "chat_key_present",
    "embeddings_available",
    "Database",
    "get_db",
    "reset_db",
    "set_db",
    "MeridianError",
    "PolicyViolation",
    "VendorError",
    "CompensationError",
    "BUS",
    "Event",
    "EventBus",
    "emit",
    "idempotent",
    "make_key",
    "PolicyContext",
    "requires_state",
    "assert_no_prohibited_basis",
    "Saga",
    "SagaStep",
]
