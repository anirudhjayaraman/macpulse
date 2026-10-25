"""macpulse: performance diagnostics and history for macOS."""

__version__ = "0.1.1"

from .checks import Finding, evaluate, health_score
from .collectors import collect

__all__ = ["collect", "evaluate", "health_score", "Finding", "__version__"]
