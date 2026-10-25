"""macverdict: tells you why your Mac was slow, and what to do about it. (Formerly macpulse.)"""

__version__ = "0.2.0"

from .checks import Finding, evaluate, health_score
from .collectors import collect

__all__ = ["collect", "evaluate", "health_score", "Finding", "__version__"]
