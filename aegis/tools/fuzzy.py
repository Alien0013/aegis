"""Fuzzy matching for edit_file: auto-recover the common LLM edit failures.

When the exact ``old_string`` isn't in the file, try progressively looser
strategies — each must produce exactly ONE match to be trusted:

  1. line-trimmed        — strip per-line leading/trailing whitespace
  2. whitespace-collapsed — collapse runs of spaces/tabs inside lines
  3. indentation-blind   — ignore leading indentation entirely
  4. block-anchor        — match by first+last line (3+ line blocks only)

Returns the EXACT substring of the file corresponding to the match, so the
caller can do a plain ``str.replace`` while preserving the file's real
whitespace. Replacement text gets the matched block's indentation re-applied.
"""

from __future__ import annotations


def _lines_with_spans(text: str) -> list[tuple[str, int, int]]:
    """Each line with its (start, end) char offsets in ``text`` (end excludes newline)."""
    out, pos = [], 0
    for line in text.split("\n"):
        out.append((line, pos, pos + len(line)))
        pos += len(line) + 1
    return out


def _find_block(text: str, needle_lines: list[str], norm) -> list[tuple[int, int]]:
    """Spans of consecutive-line windows where norm(window) == norm(needle)."""
    lines = _lines_with_spans(text)
    n = len(needle_lines)
    want = [norm(x) for x in needle_lines]
    hits = []
    for i in range(len(lines) - n + 1):
        if [norm(lines[i + j][0]) for j in range(n)] == want:
            hits.append((lines[i][1], lines[i + n - 1][2]))
    return hits


def find_fuzzy(text: str, old: str) -> tuple[str, str] | None:
    """Return (exact_substring_in_text, strategy) for a UNIQUE fuzzy match, else None."""
    needle = old.strip("\n").split("\n")
    if not any(ln.strip() for ln in needle):
        return None

    strategies = [
        ("line-trimmed", lambda s: s.strip()),
        ("whitespace-collapsed", lambda s: " ".join(s.split())),
        ("indentation-blind", lambda s: s.rstrip().lstrip(" \t")),
    ]
    for name, norm in strategies:
        hits = _find_block(text, needle, norm)
        if len(hits) == 1:
            s, e = hits[0]
            return text[s:e], name
        if len(hits) > 1:
            return None                      # ambiguous — refuse to guess

    if len(needle) >= 3:                     # block anchor: first + last line only
        lines = _lines_with_spans(text)
        first, last = needle[0].strip(), needle[-1].strip()
        hits = []
        for i in range(len(lines)):
            if lines[i][0].strip() != first:
                continue
            for j in range(i + 2, min(i + len(needle) + 8, len(lines))):
                if lines[j][0].strip() == last:
                    hits.append((lines[i][1], lines[j][2]))
                    break
        if len(hits) == 1:
            s, e = hits[0]
            return text[s:e], "block-anchor"
    return None


def reindent(replacement: str, matched: str, original_old: str) -> str:
    """Shift ``replacement``'s indentation by the same delta the matched block has
    relative to the model's ``old_string`` (so an indent-blind match still produces
    correctly indented output)."""
    def indent_of(block: str) -> str:
        for ln in block.split("\n"):
            if ln.strip():
                return ln[: len(ln) - len(ln.lstrip())]
        return ""
    got, expected = indent_of(matched), indent_of(original_old)
    if got == expected:
        return replacement
    out = []
    for ln in replacement.split("\n"):
        if ln.startswith(expected) and expected:
            out.append(got + ln[len(expected):])
        elif ln.strip() and not expected:
            out.append(got + ln)
        else:
            out.append(ln)
    return "\n".join(out)
