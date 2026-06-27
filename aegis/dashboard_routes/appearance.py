"""Dashboard appearance compatibility routes."""

from __future__ import annotations

from ..dashboard_fastapi import JSONResponse, Request, _require_request

_BUILTIN_THEMES = [
    {"name": "aegis-dark", "label": "AEGIS Teal", "description": "Deep teal operator console with ivory controls"},
    {"name": "aegis-light", "label": "AEGIS Light", "description": "Clean ink-on-paper with a cobalt accent"},
    {"name": "midnight", "label": "Midnight", "description": "Blue-violet with cool neon accents"},
    {"name": "ember", "label": "Ember", "description": "Warm crimson and bronze — forge vibes"},
    {"name": "mono", "label": "Mono", "description": "Minimal grayscale — maximum focus"},
    {"name": "cyberpunk", "label": "Cyberpunk", "description": "Neon green on black — matrix terminal"},
    {"name": "rose", "label": "Rosé", "description": "Soft pink and ivory — easy on the eyes"},
    {"name": "nord", "label": "Nord", "description": "Arctic blues — calm and balanced"},
    {"name": "dracula", "label": "Dracula", "description": "Purple and pink on slate — the classic"},
    {"name": "gruvbox", "label": "Gruvbox", "description": "Retro warm earth tones — cozy and readable"},
    {"name": "solarized", "label": "Solarized", "description": "Precision teal on deep navy — low fatigue"},
    {"name": "latte", "label": "Latte", "description": "Soft pastel light — gentle daytime mode"},
]
_THEME_NAMES = {row["name"] for row in _BUILTIN_THEMES} | {"system"}
_FONT_DEFAULT_ID = "theme"
_FONT_CHOICES = {
    "theme",
    "system-sans",
    "system-serif",
    "system-mono",
    "inter",
    "ibm-plex-sans",
    "work-sans",
    "atkinson-hyperlegible",
    "dm-sans",
    "spectral",
    "fraunces",
    "source-serif",
    "jetbrains-mono",
    "ibm-plex-mono",
    "space-mono",
}


def _active_theme(config) -> str:
    theme = str(config.get("display.theme", "system") or "system")
    return theme if theme in _THEME_NAMES else "system"


def _active_font(config) -> str:
    font = str(config.get("display.font", _FONT_DEFAULT_ID) or _FONT_DEFAULT_ID)
    return font if font in _FONT_CHOICES else _FONT_DEFAULT_ID


def register(app, config, chat_runner):  # noqa: ARG001
    @app.get("/api/dashboard/themes")
    async def api_dashboard_themes(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse({"themes": _BUILTIN_THEMES, "active": _active_theme(config)})

    @app.put("/api/dashboard/theme")
    async def api_dashboard_theme_set(request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        requested = str((body if isinstance(body, dict) else {}).get("name") or "system").strip() or "system"
        theme = requested if requested in _THEME_NAMES else "system"
        config.set("display.theme", theme)
        return JSONResponse({"ok": True, "theme": theme})

    @app.get("/api/dashboard/font")
    async def api_dashboard_font(request: Request) -> JSONResponse:
        _require_request(request, config)
        return JSONResponse({"font": _active_font(config)})

    @app.put("/api/dashboard/font")
    async def api_dashboard_font_set(request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        requested = str((body if isinstance(body, dict) else {}).get("font") or _FONT_DEFAULT_ID).strip()
        font = requested if requested in _FONT_CHOICES else _FONT_DEFAULT_ID
        config.set("display.font", font)
        return JSONResponse({"ok": True, "font": font})
