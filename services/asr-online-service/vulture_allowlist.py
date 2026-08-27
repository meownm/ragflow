"""Vulture allowlist for known false positives.

Keep this file short and add entries only with a justification comment.
"""

# FastAPI event handlers are used via decorators, not direct references.
_startup = None
_shutdown = None
