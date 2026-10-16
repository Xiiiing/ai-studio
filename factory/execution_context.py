"""Per-run evidence identity; never used as an execution authority."""
from contextvars import ContextVar

current_execution = ContextVar("factory_execution", default=None)
