"""harvest: turn a bilibili URL into a timeline-aligned transcript + visual-notes bundle."""

__version__ = "0.1.0"

# Deferred (E402): __version__ must be defined first, since config.py (imported transitively
# by probe/schema below) reads it back via `from . import __version__`.
from .probe import probe  # noqa: E402
from .schema import ProbeResult  # noqa: E402
from .providers import bilibili as _bilibili  # noqa: F401,E402  (registers BilibiliProvider)
from .providers import youtube as _youtube    # noqa: F401,E402  (registers YouTubeProvider)

__all__ = ["__version__", "probe", "ProbeResult"]
