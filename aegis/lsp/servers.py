"""Language-server registry: which server handles a file, how to start it,
where its project root is, and how to install it when missing.

Config overrides live under ``lsp.servers`` — mapping an extension to a command
line replaces the bundled choice for that extension entirely.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field

from .workspace import nearest_root


@dataclass
class ServerDef:
    id: str
    extensions: tuple[str, ...]
    language_id: str
    command: list[str]                       # argv; argv[0] looked up on PATH (or installed)
    root_markers: tuple[str, ...] = ()       # walked up from the file; workspace root fallback
    install: tuple[str, str, str] | None = None   # (kind: npm|pip|go, package, binary)
    extra_language_ids: dict = field(default_factory=dict)   # ext -> languageId override

    def language_for(self, ext: str) -> str:
        return self.extra_language_ids.get(ext, self.language_id)

    def root(self, file_path: str, workspace: str) -> str:
        if self.root_markers:
            found = nearest_root(file_path, list(self.root_markers), ceiling=workspace)
            if found:
                return found
        return workspace


SERVERS: list[ServerDef] = [
    ServerDef("pyright", (".py", ".pyi"), "python",
              ["pyright-langserver", "--stdio"],
              ("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt"),
              ("npm", "pyright", "pyright-langserver")),
    ServerDef("typescript", (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"), "typescript",
              ["typescript-language-server", "--stdio"],
              ("tsconfig.json", "package.json"),
              ("npm", "typescript-language-server typescript", "typescript-language-server"),
              {".js": "javascript", ".jsx": "javascriptreact", ".tsx": "typescriptreact",
               ".mjs": "javascript", ".cjs": "javascript"}),
    ServerDef("gopls", (".go",), "go", ["gopls"], ("go.mod",),
              ("go", "golang.org/x/tools/gopls@latest", "gopls")),
    ServerDef("rust-analyzer", (".rs",), "rust", ["rust-analyzer"], ("Cargo.toml",)),
    ServerDef("clangd", (".c", ".h", ".cpp", ".hpp", ".cc", ".cxx"), "cpp",
              ["clangd", "--background-index"],
              ("compile_commands.json", "CMakeLists.txt", "Makefile"),
              None, {".c": "c", ".h": "c"}),
    ServerDef("bash", (".sh", ".bash"), "shellscript",
              ["bash-language-server", "start"], (),
              ("npm", "bash-language-server", "bash-language-server")),
    ServerDef("yaml", (".yaml", ".yml"), "yaml",
              ["yaml-language-server", "--stdio"], (),
              ("npm", "yaml-language-server", "yaml-language-server")),
    ServerDef("php", (".php",), "php",
              ["intelephense", "--stdio"], ("composer.json",),
              ("npm", "intelephense", "intelephense")),
    ServerDef("lua", (".lua",), "lua", ["lua-language-server"], (".luarc.json",)),
    ServerDef("terraform", (".tf", ".tfvars"), "terraform",
              ["terraform-ls", "serve"], (".terraform",)),
    ServerDef("docker", ("dockerfile",), "dockerfile",
              ["docker-langserver", "--stdio"], (),
              ("npm", "dockerfile-language-server-nodejs", "docker-langserver")),
    ServerDef("zls", (".zig",), "zig", ["zls"], ("build.zig",)),
    ServerDef("ruby", (".rb",), "ruby", ["solargraph", "stdio"], ("Gemfile",)),
]


def _ext_of(path: str) -> str:
    import os
    base = os.path.basename(path).lower()
    if base in ("dockerfile",) or base.startswith("dockerfile."):
        return "dockerfile"
    _, dot, ext = base.rpartition(".")
    return f".{ext}" if dot else base


def find_server(path: str, config=None) -> ServerDef | None:
    """The ServerDef for this file, honoring ``lsp.servers`` config overrides."""
    ext = _ext_of(path)
    overrides = (config.get("lsp.servers", {}) if config else {}) or {}
    if ext in overrides:
        cmd = str(overrides[ext]).split()
        base = next((s for s in SERVERS if ext in s.extensions), None)
        return ServerDef(f"custom:{cmd[0]}", (ext,),
                         base.language_for(ext) if base else "plaintext", cmd,
                         base.root_markers if base else ())
    return next((s for s in SERVERS if ext in s.extensions), None)


def resolve_binary(sd: ServerDef, config=None, *, block: bool = True) -> str | None:
    """Absolute path to the server binary: PATH first, then our managed install dir,
    then (when allowed) a one-shot auto-install. ``block=False`` kicks the install
    off in the background and returns None — edit-time feedback must stay fast."""
    found = shutil.which(sd.command[0])
    if found:
        return found
    from .install import existing_binary, try_install
    found = existing_binary(sd.command[0])
    if found:
        return found
    if sd.install and (config is None or config.get("lsp.auto_install", True)):
        if block:
            return try_install(*sd.install)
        import threading
        threading.Thread(target=try_install, args=sd.install, daemon=True).start()
    return None
