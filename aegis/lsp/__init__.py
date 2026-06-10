"""Persistent language-server intelligence for AEGIS.

A process-wide :class:`~aegis.lsp.service.LSPService` keeps one language server
alive per (server, project root), feeds it document updates, and answers
hover/definition/references/rename queries plus — the part that matters most —
**edit diagnostics deltas**: the write/edit tools snapshot a baseline before an
edit and report only the *new* problems the edit introduced.
"""

from __future__ import annotations

_SERVICE = None


def get_service(config=None):
    """The process-wide LSP service (created lazily)."""
    global _SERVICE
    if _SERVICE is None:
        from .service import LSPService
        _SERVICE = LSPService(config)
    elif config is not None:
        _SERVICE.config = config
    return _SERVICE


def shutdown() -> None:
    global _SERVICE
    if _SERVICE is not None:
        _SERVICE.shutdown()
        _SERVICE = None
