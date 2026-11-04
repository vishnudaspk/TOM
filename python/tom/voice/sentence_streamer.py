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
    """Buffer token deltas and emit speakable sentences at true boundaries.

    P1.a First-chunk policy:
    - Flush at first natural clause/punctuation boundary.
    - If punctuation is delayed, flush at approximately 6–8 words.
    - Preserve sentence integrity where practical (prefer complete sentence if boundary present).
    - Avoid flushing tiny fragments (< 15 chars or < 3 words on clause boundaries).
    """

    def __init__(
        self,
        min_sentence_chars: int = 20,
        first_chunk_policy: bool = True,
        first_chunk_words: int = 7,
        min_clause_chars: int = 15,
        min_clause_words: int = 3,
    ) -> None:
        self._min = min_sentence_chars
        self._first_chunk_policy = first_chunk_policy
        self._first_chunk_words = first_chunk_words
        self._min_clause_chars = min_clause_chars
        self._min_clause_words = min_clause_words
        self._buf = ""
        self._first_emitted = False

    def reset(self) -> None:
        """Reset streamer state for a new turn."""
        self._buf = ""
        self._first_emitted = False

    def push_chunk(self, token: str) -> list[str]:
        if not token:
            return []
        self._buf += token
        return self._drain(flush=False)

    def flush(self) -> list[str]:
        out = self._drain(flush=True)
        self._first_emitted = False
        return out

    def _find_first_sentence_boundary(self, buf: str, *, flush: bool) -> int | None:
        """Return index after first complete sentence boundary, if any."""
        for i, ch in enumerate(buf):
            if ch == "\n":
                return i + 1
            if ch in ".!?" and self._is_boundary(buf, i, flush=flush):
                end = i + 1
                while end < len(buf) and buf[end] in " \t\r":
                    end += 1
                return end
        return None

    def _find_first_clause_boundary(self, buf: str) -> int | None:
        """Find index after first clause boundary (,;:—) if speakable."""
        clause_puncts = {",", ";", ":", "—"}
        i = 0
        while i < len(buf):
            ch = buf[i]
            matched_len = 0
            if ch in clause_puncts:
                matched_len = 1
            elif ch == "-" and i + 1 < len(buf) and buf[i + 1] == "-":
                matched_len = 2

            if matched_len > 0:
                end = i + matched_len
                # Must be followed by whitespace or end of buf
                if end < len(buf) and buf[end] not in " \t\r\n":
                    i += matched_len
                    continue
                candidate = buf[:end].strip()
                words = candidate.split()
                if (
                    len(candidate) >= self._min_clause_chars
                    and len(words) >= self._min_clause_words
                ):
                    return end
                i += matched_len
                continue
            i += 1
        return None

    def _find_delayed_word_boundary(self, buf: str) -> int | None:
        """Find boundary after ~6-8 completed words when punctuation is delayed."""
        words = buf.split()
        completed_words = words if (buf and buf[-1].isspace()) else words[:-1]
        if len(completed_words) >= self._first_chunk_words:
            target_words = completed_words[: self._first_chunk_words]
            pos = 0
            for w in target_words:
                idx = buf.find(w, pos)
                if idx == -1:
                    return None
                pos = idx + len(w)
            while pos < len(buf) and buf[pos] in " \t\r":
                pos += 1
            return pos
        return None

    def _drain(self, *, flush: bool) -> list[str]:
        buf = self._buf
        out: list[str] = []

        # P1.a: Check first-chunk policy when enabled, no chunk emitted yet,
        # and not in a forced terminal flush.
        if self._first_chunk_policy and not self._first_emitted and not flush:
            sent_end = self._find_first_sentence_boundary(buf, flush=flush)
            sent_words = len(buf[:sent_end].split()) if sent_end is not None else 0

            # If a complete sentence is already available and <= 12 words,
            # preserve sentence integrity by letting standard sentence loop handle it.
            if sent_end is not None and sent_words <= 12:
                pass
            else:
                # Punctuation delayed or long sentence:
                # 1. Try clause boundary (, ; : —)
                clause_end = self._find_first_clause_boundary(buf)
                if clause_end is not None and (sent_end is None or clause_end < sent_end):
                    piece = buf[:clause_end].strip()
                    if piece:
                        out.append(piece)
                        self._buf = buf[clause_end:].lstrip()
                        self._first_emitted = True
                        return out

                # 2. Try delayed punctuation (6-8 words)
                word_end = self._find_delayed_word_boundary(buf)
                if word_end is not None and (sent_end is None or word_end < sent_end):
                    piece = buf[:word_end].strip()
                    if piece:
                        out.append(piece)
                        self._buf = buf[word_end:].lstrip()
                        self._first_emitted = True
                        return out

        # Standard sentence boundary processing
        start = 0
        i = 0
        min_threshold = (
            self._min_clause_chars
            if (self._first_chunk_policy and not self._first_emitted)
            else self._min
        )
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
                if flush or len(piece) >= min_threshold:
                    out.append(piece)
                    self._first_emitted = True
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
