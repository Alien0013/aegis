"""Small shell-command helpers shared by terminal tools."""

from __future__ import annotations


def validate_command(value) -> tuple[str | None, str | None]:
    """Return (command, error). Commands must be non-empty strings."""
    if not isinstance(value, str):
        return None, f"Invalid command: expected string, got {type(value).__name__}"
    if not value.strip():
        return None, "Invalid command: empty command"
    return value, None


def _read_shell_token(command: str, start: int) -> tuple[str, int]:
    i = start
    n = len(command)
    while i < n:
        ch = command[i]
        if ch.isspace() or ch in ";|&()":
            break
        if ch == "'":
            i += 1
            while i < n and command[i] != "'":
                i += 1
            if i < n:
                i += 1
            continue
        if ch == '"':
            i += 1
            while i < n:
                inner = command[i]
                if inner == "\\" and i + 1 < n:
                    i += 2
                    continue
                if inner == '"':
                    i += 1
                    break
                i += 1
            continue
        if ch == "\\" and i + 1 < n:
            i += 2
            continue
        i += 1
    return command[start:i], i


def rewrite_compound_background(command: str) -> str:
    """Rewrite `A && B &` / `A || B &` to avoid bash's subshell-wait leak."""
    n = len(command)
    i = 0
    paren_depth = 0
    brace_depth = 0
    last_chain_op_end = -1
    rewrites: list[tuple[int, int]] = []

    while i < n:
        ch = command[i]
        if ch == "\n" and paren_depth == 0 and brace_depth == 0:
            last_chain_op_end = -1
            i += 1
            continue
        if ch.isspace():
            i += 1
            continue
        if ch == "#":
            nl = command.find("\n", i)
            if nl == -1:
                break
            i = nl
            continue
        if ch == "\\" and i + 1 < n:
            i += 2
            continue
        if ch in {"'", '"'}:
            _, next_i = _read_shell_token(command, i)
            i = max(next_i, i + 1)
            continue
        if ch == "(":
            paren_depth += 1
            i += 1
            continue
        if ch == ")":
            paren_depth = max(0, paren_depth - 1)
            i += 1
            continue
        if ch == "{" and i + 1 < n and (command[i + 1].isspace() or command[i + 1] == "\n"):
            brace_depth += 1
            i += 1
            continue
        if ch == "}" and brace_depth > 0:
            brace_depth -= 1
            last_chain_op_end = -1
            i += 1
            continue
        if paren_depth > 0 or brace_depth > 0:
            i += 1
            continue
        if command.startswith("&&", i) or command.startswith("||", i):
            last_chain_op_end = i + 2
            i += 2
            continue
        if ch == ";":
            last_chain_op_end = -1
            i += 1
            continue
        if ch == "|":
            last_chain_op_end = -1
            i += 1
            continue
        if ch == "&":
            if i + 1 < n and command[i + 1] == ">":
                i += 2
                continue
            j = i - 1
            while j >= 0 and command[j].isspace():
                j -= 1
            if j >= 0 and command[j] in "<>":
                i += 1
                continue
            if last_chain_op_end >= 0:
                rewrites.append((last_chain_op_end, i))
            last_chain_op_end = -1
            i += 1
            continue
        _, next_i = _read_shell_token(command, i)
        i = max(next_i, i + 1)

    if not rewrites:
        return command

    result = command
    for chain_end, amp_pos in reversed(rewrites):
        insert_pos = chain_end
        while insert_pos < amp_pos and result[insert_pos].isspace():
            insert_pos += 1
        result = (
            result[:insert_pos]
            + "{ "
            + result[insert_pos:amp_pos]
            + "& }"
            + result[amp_pos + 1:]
        )
    return result
