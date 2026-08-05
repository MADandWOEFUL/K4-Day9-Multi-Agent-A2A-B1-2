"""agents package — multi-agent e-commerce dispute resolution."""
from .base import IN_DIR, OUT_DIR, LOG_DIR  # noqa: F401
from .coordinator import Coordinator
from .data_loader import DataIndex


def build_pipeline() -> Coordinator:
    index = DataIndex()
    return Coordinator(index)
