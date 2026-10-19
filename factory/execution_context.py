"""Role evidence identity and the actual worker lease context."""
from contextvars import ContextVar

current_execution = ContextVar("factory_execution", default=None)

# Actual worker authority, separate from per-role evidence contexts.
current_job = ContextVar('factory_current_job', default=None)
