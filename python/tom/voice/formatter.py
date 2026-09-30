"""Deterministic speech formatter for LLM response text.

Converts assistant markdown responses into clean, natural plain text suitable
for speech synthesis (TTS). Pure Python, stdlib-only, deterministic.

Adheres to:
- Phase 6, Iteration 2: SpeechFormatter
- Decision 041: Decoupled utterance-buffered voice architecture
- skills/coding/validation: Strict bounds, no external dependencies.
"""

from __future__ import annotations

import re


class SpeechFormatter:
    """Formats assistant text responses into speech-friendly natural language.

    Removes Markdown syntax, normalizes whitespace, expands symbols,
    and strips elements that sound unnatural when spoken by a TTS engine.
    """

    def __init__(
        self,
        max_chars: int | None = None,
        strip_code_blocks: bool = False,
        code_replacement: str = "",
    ) -> None:
        """Initialize the formatter.

        Args:
            max_chars: Optional maximum character count to bound TTS latency.
                When set, truncates text at a natural sentence or word break.
            strip_code_blocks: If True, completely drops fenced code blocks
                instead of stripping fences and keeping the inner content.
            code_replacement: Replacement text when strip_code_blocks is True.
        """
        self.max_chars = max_chars
        self.strip_code_blocks = strip_code_blocks
        self.code_replacement = code_replacement

    def format(
        self,
        text: str,
        max_chars: int | None = None,
        strip_code_blocks: bool | None = None,
    ) -> str:
        """Transform markdown / LLM text into speakable plain text.

        Args:
            text: Raw assistant response string.
            max_chars: Override instance max_chars if provided.
            strip_code_blocks: Override instance strip_code_blocks if provided.

        Returns:
            Speech-friendly cleaned text string.
        """
        if not text or not text.strip():
            return ""

        should_strip_code = (
            self.strip_code_blocks if strip_code_blocks is None else strip_code_blocks
        )
        limit_chars = self.max_chars if max_chars is None else max_chars

        cleaned = text

        # 1. Fenced code blocks
        if should_strip_code:
            cleaned = re.sub(
                r"```[a-zA-Z0-9_-]*\n?[\s\S]*?```",
                self.code_replacement,
                cleaned,
            )
        else:
            # Strip fence delimiters, keeping the code/text content inside
            cleaned = re.sub(r"```[a-zA-Z0-9_-]*\n?", "", cleaned)
            cleaned = cleaned.replace("```", "")

        # 2. HTML tags
        cleaned = re.sub(r"<[^>]+>", " ", cleaned)

        # 3. Horizontal rules (---, ***, ___)
        cleaned = re.sub(r"^[ \t]*[-*_]{3,}[ \t]*$", "", cleaned, flags=re.MULTILINE)

        # 4. Images: ![alt](url) -> alt
        cleaned = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", cleaned)

        # 5. Links: [text](url) -> text, [text][ref] -> text
        cleaned = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", cleaned)
        cleaned = re.sub(r"\[([^\]]+)\]\[[^\]]*\]", r"\1", cleaned)

        # 6. Inline code: `code` -> code
        cleaned = re.sub(r"`([^`]+)`", r"\1", cleaned)

        # 7. Tables: table header separators (|---|---|) removed, row pipes replaced by space
        cleaned = re.sub(r"^[ \t]*\|?(\s*:?---+:?\s*\|)+\s*$", "", cleaned, flags=re.MULTILINE)
        cleaned = re.sub(r"^[ \t]*\|", "", cleaned, flags=re.MULTILINE)
        cleaned = re.sub(r"\|[ \t]*$", "", cleaned, flags=re.MULTILINE)
        cleaned = cleaned.replace("|", ", ")

        # 8. Process line by line for structural markers (headings, blockquotes, lists)
        lines = cleaned.splitlines()
        processed_lines: list[str] = []

        for raw_line in lines:
            line = raw_line.strip()
            if not line:
                continue

            # Blockquotes: > quote -> quote
            if line.startswith(">"):
                line = re.sub(r"^>[ \t]*", "", line).strip()

            # Headings: # Heading -> Heading.
            heading_match = re.match(r"^#{1,6}\s+(.+)$", line)
            if heading_match:
                content = heading_match.group(1).strip()
                if content and content[-1] not in ".!?:;,":
                    content += "."
                processed_lines.append(content)
                continue

            # Bullet list: - Item -> Item.
            bullet_match = re.match(r"^[-*+]\s+(.+)$", line)
            if bullet_match:
                content = bullet_match.group(1).strip()
                if content and content[-1] not in ".!?:;,":
                    content += "."
                processed_lines.append(content)
                continue

            # Numbered list: 1. Item -> Item.
            num_match = re.match(r"^\d+[\.)]\s+(.+)$", line)
            if num_match:
                content = num_match.group(1).strip()
                if content and content[-1] not in ".!?:;,":
                    content += "."
                processed_lines.append(content)
                continue

            processed_lines.append(line)

        cleaned = " ".join(processed_lines)

        # 9. Strikethrough: ~~text~~ -> text
        cleaned = re.sub(r"~~([^~]+)~~", r"\1", cleaned)

        # 10. Bold & Italic (***, ___, **, __, *, _)
        cleaned = re.sub(r"\*\*\*([^*\n]+?)\*\*\*", r"\1", cleaned)
        cleaned = re.sub(r"___([^_\n]+?)___", r"\1", cleaned)
        cleaned = re.sub(r"\*\*([^*\n]+?)\*\*", r"\1", cleaned)
        cleaned = re.sub(r"__([^_\n]+?)__", r"\1", cleaned)
        cleaned = re.sub(r"\*(?!\s)([^*\n]+?)(?<!\s)\*", r"\1", cleaned)
        # Underscore for italic only when surrounded by non-word/space to avoid snake_case
        cleaned = re.sub(r"(?<!\w)_(?![\s_])([^_\n]+?)(?<![\s_])_(?!\w)", r"\1", cleaned)

        # 11. Symbol expansions for spoken clarity
        # % -> percent
        cleaned = re.sub(r"(\d+)\s*%", r"\1 percent", cleaned)
        cleaned = cleaned.replace("%", " percent")
        # & -> and
        cleaned = re.sub(r"(?<=\w)\s*&\s*(?=\w)", " and ", cleaned)
        cleaned = cleaned.replace(" & ", " and ")
        # Abbreviations: w/o first, then w/
        cleaned = re.sub(r"(^|\s)w/o([\s,.!?;:]|$)", r"\1without\2", cleaned)
        cleaned = re.sub(r"(^|\s)w/([\s,.!?;:]|$)", r"\1with\2", cleaned)

        # 12. Punctuation spacing and whitespace normalization
        # Collapse multiple spaces
        cleaned = re.sub(r"[ \t]+", " ", cleaned)
        # Fix spacing before punctuation
        cleaned = re.sub(r"\s+([,.!?;:])", r"\1", cleaned)
        # Remove consecutive duplicate punctuation like '..' or ',,'
        cleaned = re.sub(r"\.{2,}", ".", cleaned)
        cleaned = re.sub(r",{2,}", ",", cleaned)
        # Ensure single space after punctuation if followed by a letter
        cleaned = re.sub(r"([,.!?;:])([a-zA-Z])", r"\1 \2", cleaned)

        cleaned = cleaned.strip()

        # 13. Truncation if max_chars is set
        if limit_chars is not None and limit_chars > 0 and len(cleaned) > limit_chars:
            cleaned = self._truncate(cleaned, limit_chars)

        return cleaned

    def _truncate(self, text: str, max_chars: int) -> str:
        """Truncate text at the nearest sentence or word boundary."""
        if len(text) <= max_chars:
            return text

        candidate = text[:max_chars]

        # Try to break at the last sentence boundary within candidate (. ! ?)
        last_punct = -1
        for p in (". ", "! ", "? "):
            pos = candidate.rfind(p)
            if pos != -1 and (pos + 1) > last_punct:
                last_punct = pos + 1

        if last_punct > 0:
            return candidate[:last_punct].strip()

        # Fallback to word boundary
        last_space = candidate.rfind(" ")
        if last_space > 0:
            truncated = candidate[:last_space].rstrip(",;:-")
            if truncated and truncated[-1] not in ".!?":
                truncated += "..."
            return truncated

        # Hard truncate
        return candidate.rstrip(",;:-") + "..."


__all__ = ["SpeechFormatter"]
