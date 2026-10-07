"""Incremental sentence chunker for speakable TTS units.

Phase 9 Iteration 9.2. Deterministic; no NLP / LLM.
"""

from __future__ import annotations

_ABBREVS = frozenset(
    {
        "mr",
        "mrs",
        "ms",
        "dr",
        "sr",
        "jr",
        "vs",
        "etc",
        "inc",
        "ltd",
        "e.g",
        "i.e",
        "u.s",
        "u.k",
        "prof",
        "st",
        "approx",
        "fig",
        "al",
    }
)


class SentenceStreamer:
    """Buffer token deltas and emit speakable sentences at true boundaries."""

    def __init__(self, min_sentence_chars: int = 20) -> None:
        self._min = min_sentence_chars
        self._buf = ""

    def push_chunk(self, token: str) -> list[str]:
        if not token:
            return []
        self._buf += token
        return self._drain(flush=False)

    def flush(self) -> list[str]:
        return self._drain(flush=True)

    def _drain(self, *, flush: bool) -> list[str]:
        buf = self._buf
        out: list[str] = []
        start = 0
        i = 0
        while i < len(buf):
            ch = buf[i]
            if ch == "\n" or ch in ".!?":
                if ch != "\n" and not self._is_boundary(buf, i, flush=flush):
                    i += 1
                    continue
                end = i + 1
                while end < len(buf) and buf[end] in " \t\r":
                    end += 1
                piece = buf[start:end].strip()
                if not piece:
                    start = end
                    i = end
                    continue
                if flush or len(piece) >= self._min:
                    out.append(piece)
                    start = end
                i = end
                continue
            i += 1
        self._buf = buf[start:]
        if flush:
            rest = self._buf.strip()
            self._buf = ""
            if rest:
                out.append(rest)
        return out

    def _is_boundary(self, buf: str, i: int, *, flush: bool) -> bool:
        ch = buf[i]
        if ch in "!?":
            return True
        if ch != ".":
            return False
        nxt = buf[i + 1] if i + 1 < len(buf) else ""
        if nxt and nxt not in " \t\r\n":
            return False
        if self._abbrev_before(buf, i):
            return False
        if not nxt:
            if i > 0 and buf[i - 1].isdigit() and not flush:
                return False
            return True
        return True

    @staticmethod
    def _abbrev_before(buf: str, i: int) -> bool:
        prefix = buf[:i]
        j = len(prefix) - 1
        while j >= 0 and (prefix[j].isalpha() or prefix[j] == "."):
            j -= 1
        token = prefix[j + 1 :].lower()
        return token in _ABBREVS
