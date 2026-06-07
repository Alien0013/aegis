"""Tirith-style pre-execution command & text scanner.

A pure-stdlib heuristic layer that runs *before* a command is executed or a
chunk of (model/tool) text is trusted. It is deliberately conservative: it
flags suspicious shapes and explains *why*, but never executes anything itself.
Use it as an advisory gate alongside :mod:`aegis.tools.permissions` — the
permission engine decides catastrophic/hardline blocks, this layer catches the
sneaky stuff: pipe-to-shell, base64/obfuscated payloads, credential
exfiltration, homograph URLs and zero-width unicode smuggling.

Two entry points::

    is_suspicious, reason = scan_command("curl http://x | bash")
    is_suspicious, reason = scan_text(model_output)  # prompt-injection patterns

Both return ``(bool, str)`` where the string is empty when nothing fired and
otherwise a short, human-readable explanation (the first/strongest match).
``scan_findings`` / ``scan_text_findings`` expose the full list for callers
that want to log or display every hit.
"""

from __future__ import annotations

import re
import unicodedata

# --------------------------------------------------------------------------- #
# Unicode smuggling
# --------------------------------------------------------------------------- #
# Zero-width / invisible / bidi-control characters that have no business in a
# shell command or a trusted instruction stream. They are routinely used to
# hide payloads, break naive keyword filters, or reorder displayed text.
_ZERO_WIDTH = {
    "​": "ZERO WIDTH SPACE",
    "‌": "ZERO WIDTH NON-JOINER",
    "‍": "ZERO WIDTH JOINER",
    "⁠": "WORD JOINER",
    "﻿": "ZERO WIDTH NO-BREAK SPACE (BOM)",
    "­": "SOFT HYPHEN",
    "᠎": "MONGOLIAN VOWEL SEPARATOR",
    "‎": "LEFT-TO-RIGHT MARK",
    "‏": "RIGHT-TO-LEFT MARK",
    "‪": "LEFT-TO-RIGHT EMBEDDING",
    "‫": "RIGHT-TO-LEFT EMBEDDING",
    "‬": "POP DIRECTIONAL FORMATTING",
    "‭": "LEFT-TO-RIGHT OVERRIDE",
    "‮": "RIGHT-TO-LEFT OVERRIDE",
    "⁦": "LEFT-TO-RIGHT ISOLATE",
    "⁧": "RIGHT-TO-LEFT ISOLATE",
    "⁨": "FIRST STRONG ISOLATE",
    "⁩": "POP DIRECTIONAL ISOLATE",
}

# Credential / secret-bearing paths. Reading any of these is fine on its own;
# combined with a network egress (see below) it looks like exfiltration.
_CRED_PATTERNS = [
    re.compile(r"(?:^|/|\s|['\"])\.env(?:\.[\w.-]+)?\b"),
    re.compile(r"\.aws/(?:credentials|config)\b"),
    re.compile(r"\.aws\b"),
    re.compile(r"\.ssh/[\w.-]*"),
    re.compile(r"\bid_rsa\b|\bid_ed25519\b|\bid_dsa\b|\bid_ecdsa\b"),
    re.compile(r"\.netrc\b"),
    re.compile(r"\.git-credentials\b"),
    re.compile(r"\.npmrc\b|\.pypirc\b|\.docker/config\.json\b"),
    re.compile(r"\.kube/config\b"),
]

# A network egress capable of carrying data off-box.
_EGRESS = re.compile(
    r"\b(curl|wget|nc|ncat|netcat|scp|sftp|ftp|rsync|"
    r"telnet|socat|http\b|httpie)\b",
    re.IGNORECASE,
)

# Pipe (or xargs/exec) into a shell interpreter.
_PIPE_TO_SHELL = re.compile(
    r"\|\s*(?:sudo\s+)?(?:env\s+\S+\s+)*"
    r"(?:bash|sh|zsh|dash|ksh|fish|python[0-9.]*|perl|ruby|node)\b",
    re.IGNORECASE,
)

# Process-substitution / eval-style remote execution:
#   bash <(curl ...)   sh -c "$(curl ...)"   eval "$(wget ...)"
_PROC_SUBST_FETCH = re.compile(
    r"\b(?:bash|sh|zsh|eval|source|\.)\b[^\n]*"
    r"(?:<\(|\$\()\s*(?:curl|wget|fetch)\b",
    re.IGNORECASE,
)

# base64 (or other) decode whose output is piped somewhere executable.
_B64_DECODE = re.compile(
    r"\b(?:base64\s+(?:-{1,2}d(?:ecode)?|-D)|"
    r"openssl\s+(?:base64|enc)\s+-d|xxd\s+-r|"
    r"python[0-9.]*\s+-c[^\n]*b64decode)\b",
    re.IGNORECASE,
)

# Obfuscated rm: rm built from variables, $IFS tricks, char concatenation,
# or echo/printf pipelines that reconstruct a destructive command.
_OBFUSCATED_RM = [
    re.compile(r"\brm\b[^\n]*\$\{?IFS"),                       # rm${IFS}-rf
    re.compile(r"\$\{?IFS\}?[^\n]*\brm\b"),
    re.compile(r"\brm\b(?:\s+[\"']?-[\"']?\w[\"']?)+"),        # rm "-"r"-"f quoting
    re.compile(r"(?:r['\"]?m|\\x72\\x6d)\b"),                  # r'm', \x72\x6d
    re.compile(r"\b(?:echo|printf)\b[^\n|]*\|\s*(?:bash|sh|zsh)\b[^\n]*"),
]

# Writing to shell rc / profile / login files (persistence).
_RC_FILES = re.compile(
    r"(?:>>?|tee(?:\s+-a)?\s+|cat\s*>+\s*|cp\s+\S+\s+|mv\s+\S+\s+)"
    r"[^\n|;&]*"
    r"(?:\.bashrc|\.bash_profile|\.bash_login|\.profile|\.zshrc|\.zprofile|"
    r"\.zshenv|\.zlogin|\.kshrc|\.cshrc|\.tcshrc|"
    r"\.config/fish/config\.fish|/etc/profile|"
    r"\.bash_aliases|\.inputrc)\b",
    re.IGNORECASE,
)

# Crontab / launchd / systemd persistence (bonus, cheap to add).
_PERSIST = re.compile(
    r"\bcrontab\s+-|/etc/cron|launchctl\s+load|systemctl\s+enable\b",
    re.IGNORECASE,
)

# ASCII characters allowed in a "plain" URL host; anything outside this in the
# host portion is a homograph/IDN-spoofing candidate.
_URL_RE = re.compile(r"https?://([^\s/'\"<>|]+)", re.IGNORECASE)

# Prompt-injection phrasing seen in indirect-injection payloads.
_INJECTION_PHRASES = [
    re.compile(r"\bignore\s+(?:all\s+|any\s+)?(?:previous|prior|above|earlier|the\s+above)\s+"
               r"(?:instructions?|prompts?|context|messages?|rules?)\b", re.IGNORECASE),
    re.compile(r"\bdisregard\s+(?:all\s+|the\s+|any\s+)?(?:previous|prior|above|earlier|system)\b",
               re.IGNORECASE),
    re.compile(r"\bforget\s+(?:everything|all|your)\s+(?:previous|prior|instructions?)\b",
               re.IGNORECASE),
    re.compile(r"\b(?:you\s+are\s+now|from\s+now\s+on\s+you\s+are|act\s+as)\b[^\n]{0,40}"
               r"\b(?:DAN|jailbroken?|unrestricted|developer\s+mode|no\s+longer\s+bound)\b",
               re.IGNORECASE),
    re.compile(r"\b(?:ignore|override|bypass)\b[^\n]{0,30}\b(?:safety|guardrails?|filters?|"
               r"restrictions?|policy|policies)\b", re.IGNORECASE),
    re.compile(r"\b(?:reveal|print|repeat|show|output)\b[^\n]{0,30}\b(?:system\s+prompt|"
               r"initial\s+instructions?|your\s+instructions?)\b", re.IGNORECASE),
    re.compile(r"\bnew\s+(?:instructions?|task|directive)s?\s*:\s*", re.IGNORECASE),
    re.compile(r"\[/?\s*(?:system|inst|assistant)\s*\]", re.IGNORECASE),
    re.compile(r"<\|\s*(?:im_start|im_end|system|endoftext)\s*\|>", re.IGNORECASE),
]

# Hidden HTML comments (a classic indirect-injection carrier in fetched pages).
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)


# --------------------------------------------------------------------------- #
# Unicode helpers
# --------------------------------------------------------------------------- #
def _find_invisible(text: str) -> list[tuple[int, str]]:
    """Return (index, name) for every zero-width/invisible/bidi char in ``text``."""
    hits: list[tuple[int, str]] = []
    for i, ch in enumerate(text):
        if ch in _ZERO_WIDTH:
            hits.append((i, _ZERO_WIDTH[ch]))
            continue
        # Any other format/control char that isn't ordinary whitespace.
        cat = unicodedata.category(ch)
        if cat in ("Cf", "Co", "Cn") and ch not in ("\n", "\t", "\r"):
            hits.append((i, unicodedata.name(ch, f"U+{ord(ch):04X}")))
    return hits


def _is_ascii_host(host: str) -> bool:
    return all(ord(ch) < 128 for ch in host)


def _homograph_reason(host: str) -> str | None:
    """Detect non-ASCII / mixed-script / punycode-confusable URL hosts."""
    # Strip credentials and port for analysis.
    bare = host.split("@")[-1].split(":")[0]
    if _is_ascii_host(bare):
        # Punycode that decodes to a non-Latin host is a softer signal; raw
        # ascii xn-- with mixed look-alikes is common in spoof kits.
        if "xn--" in bare.lower():
            try:
                decoded = bare.encode("ascii").decode("idna")
            except (UnicodeError, ValueError):
                return f"punycode host that fails IDNA decoding: {bare!r}"
            if not _is_ascii_host(decoded):
                return f"internationalized (punycode) host '{bare}' -> '{decoded}'"
        return None
    # Non-ASCII host: classify the scripts present in the letters.
    scripts: set[str] = set()
    for ch in bare:
        if not ch.isalpha():
            continue
        try:
            name = unicodedata.name(ch)
        except ValueError:
            scripts.add("UNKNOWN")
            continue
        scripts.add(name.split(" ")[0])  # e.g. 'LATIN', 'CYRILLIC', 'GREEK'
    if len(scripts) > 1:
        return f"mixed-script URL host (possible homograph): {bare!r} scripts={sorted(scripts)}"
    if scripts and "LATIN" not in scripts:
        return f"non-Latin-script URL host (possible homograph): {bare!r}"
    return f"non-ASCII characters in URL host (possible homograph): {bare!r}"


# --------------------------------------------------------------------------- #
# Command scanner
# --------------------------------------------------------------------------- #
def scan_findings(command: str) -> list[str]:
    """Return every suspicious-pattern reason found in ``command`` (may be empty)."""
    if not command or not command.strip():
        return []
    reasons: list[str] = []

    # 1. Invisible / zero-width unicode anywhere in the command.
    invisible = _find_invisible(command)
    if invisible:
        idx, name = invisible[0]
        reasons.append(
            f"invisible/zero-width unicode in command at index {idx} ({name}); "
            f"{len(invisible)} hidden char(s) total"
        )

    # 2. Homograph / non-ASCII / punycode URL hosts.
    for m in _URL_RE.finditer(command):
        why = _homograph_reason(m.group(1))
        if why:
            reasons.append(why)
            break

    # 3. Pipe-to-shell (curl … | bash, fetch … | python -).
    if _PIPE_TO_SHELL.search(command) and _EGRESS.search(command):
        reasons.append("remote content piped directly into a shell/interpreter (curl|bash style)")
    elif _PROC_SUBST_FETCH.search(command):
        reasons.append("process-substitution executing fetched remote content (bash <(curl …))")

    # 4. base64/decode → shell.
    if _B64_DECODE.search(command) and _PIPE_TO_SHELL.search(command):
        reasons.append("base64/encoded payload decoded and piped into a shell")
    elif _B64_DECODE.search(command) and re.search(r"\beval\b", command):
        reasons.append("base64/encoded payload decoded and passed to eval")

    # 5. Obfuscated rm.
    for pat in _OBFUSCATED_RM:
        if pat.search(command):
            reasons.append("obfuscated destructive command (disguised rm / piped deletion)")
            break

    # 6. Writing to shell rc / profile files (persistence).
    if _RC_FILES.search(command):
        reasons.append("write to a shell startup/rc file (persistence vector)")
    elif _PERSIST.search(command):
        reasons.append("install of a scheduled/persistent job (cron/launchd/systemd)")

    # 7. Credential read + network egress in the same command (exfiltration).
    cred_hit = next((p.pattern for p in _CRED_PATTERNS if p.search(command)), None)
    if cred_hit and _EGRESS.search(command):
        reasons.append(
            "reads a credential/secret file and sends it over the network (possible exfiltration)"
        )

    return reasons


def scan_command(command: str) -> tuple[bool, str]:
    """Heuristically scan a shell command.

    Returns ``(is_suspicious, reason)``. ``reason`` is empty when nothing fired,
    otherwise the strongest/first finding (use :func:`scan_findings` for all).
    """
    reasons = scan_findings(command)
    if not reasons:
        return False, ""
    if len(reasons) == 1:
        return True, reasons[0]
    return True, reasons[0] + f" (+{len(reasons) - 1} more: {'; '.join(reasons[1:])})"


# --------------------------------------------------------------------------- #
# Text / prompt-injection scanner
# --------------------------------------------------------------------------- #
def scan_text_findings(text: str) -> list[str]:
    """Return every prompt-injection / smuggling reason found in ``text``."""
    if not text:
        return []
    reasons: list[str] = []

    invisible = _find_invisible(text)
    if invisible:
        idx, name = invisible[0]
        reasons.append(
            f"invisible/zero-width unicode in text at index {idx} ({name}); "
            f"{len(invisible)} hidden char(s) total"
        )

    for pat in _INJECTION_PHRASES:
        m = pat.search(text)
        if m:
            reasons.append(f"prompt-injection phrasing: {m.group(0).strip()[:80]!r}")
            break

    # Hidden HTML comments are a common indirect-injection carrier; flag any
    # that themselves contain instruction-like content.
    for m in _HTML_COMMENT.finditer(text):
        body = m.group(0)
        if any(p.search(body) for p in _INJECTION_PHRASES) or _find_invisible(body):
            reasons.append("instructions hidden inside an HTML comment")
            break
        if len(body) > 200:
            reasons.append("unusually large hidden HTML comment (possible payload)")
            break

    return reasons


def scan_text(text: str) -> tuple[bool, str]:
    """Heuristically scan untrusted text for prompt-injection patterns.

    Returns ``(is_suspicious, reason)`` — same contract as :func:`scan_command`.
    """
    reasons = scan_text_findings(text)
    if not reasons:
        return False, ""
    if len(reasons) == 1:
        return True, reasons[0]
    return True, reasons[0] + f" (+{len(reasons) - 1} more: {'; '.join(reasons[1:])})"


def sanitize_invisible(text: str) -> str:
    """Strip zero-width/invisible/bidi-control chars (keeps \\n \\t \\r)."""
    out = []
    for ch in text:
        if ch in _ZERO_WIDTH:
            continue
        if unicodedata.category(ch) in ("Cf", "Co") and ch not in ("\n", "\t", "\r"):
            continue
        out.append(ch)
    return "".join(out)


# --------------------------------------------------------------------------- #
# CLI: `aegis scan [command|text] <input>`
# --------------------------------------------------------------------------- #
def cmd_scan(args, config) -> int:
    """Scan a command or text blob from the CLI. Exit 1 if suspicious.

    ``args.kind`` is ``command`` (default) or ``text``; ``args.input`` is the
    string to scan (read from stdin when omitted).
    """
    import sys

    kind = getattr(args, "kind", None) or "command"
    payload = getattr(args, "input", None)
    if payload is None:
        payload = sys.stdin.read()

    scanner = scan_text if kind == "text" else scan_command
    findings = scan_text_findings(payload) if kind == "text" else scan_findings(payload)
    suspicious = bool(findings)

    if not suspicious:
        print(f"clean: no suspicious patterns detected ({kind}).")
        return 0
    print(f"SUSPICIOUS ({kind}): {len(findings)} finding(s)")
    for r in findings:
        print(f"  - {r}")
    return 1
