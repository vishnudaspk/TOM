"""Unit tests for Phase 9 Iteration 9.2: SentenceStreamer."""

from __future__ import annotations

import time

from tom.voice.sentence_streamer import SentenceStreamer


def _push_all(text: str, min_chars: int = 20) -> list[str]:
    s = SentenceStreamer(min_sentence_chars=min_chars)
    out = s.push_chunk(text)
    out.extend(s.flush())
    return out


def _push_incremental(text: str, min_chars: int = 20) -> list[str]:
    s = SentenceStreamer(min_sentence_chars=min_chars)
    out: list[str] = []
    for ch in text:
        out.extend(s.push_chunk(ch))
    out.extend(s.flush())
    return out


class TestPunctuationSplit:
    def test_period_exclaim_question(self) -> None:
        text = "Hello there, friend. Are you ready today? Yes I am ready!"
        parts = _push_all(text)
        assert parts == [
            "Hello there, friend.",
            "Are you ready today?",
            "Yes I am ready!",
        ]

    def test_multiple_sentences_one_chunk(self) -> None:
        parts = _push_all("First sentence is ready now. Second sentence is also ready.")
        assert parts == [
            "First sentence is ready now.",
            "Second sentence is also ready.",
        ]


class TestAbbreviations:
    def test_ie_mr_vs_intact(self) -> None:
        text = "See i.e. the note from Mr. Smith vs. the other copy."
        parts = _push_all(text)
        assert parts == [text]

    def test_eg_filename_intact(self) -> None:
        text = "For example e.g. sample.py is the entry point."
        parts = _push_all(text)
        assert parts == [text]
        assert "sample.py" in parts[0]


class TestDecimals:
    def test_pi_and_version(self) -> None:
        text = "Pi is about 3.14 and version 2.0 shipped today."
        parts = _push_all(text)
        assert parts == [text]
        assert "3.14" in parts[0]
        assert "2.0" in parts[0]


class TestNewline:
    def test_newline_boundary(self) -> None:
        parts = _push_all("First sentence is ready now.\nSecond sentence is also ready.")
        assert parts == [
            "First sentence is ready now.",
            "Second sentence is also ready.",
        ]


class TestMinLength:
    def test_short_held_until_flush(self) -> None:
        s = SentenceStreamer(min_sentence_chars=20)
        assert s.push_chunk("Hi.") == []
        assert s.flush() == ["Hi."]

    def test_short_merges_with_following(self) -> None:
        s = SentenceStreamer(min_sentence_chars=20)
        assert s.push_chunk("Hi. ") == []
        out = s.push_chunk("This continues into a longer clause.")
        assert out == ["Hi. This continues into a longer clause."]


class TestFlush:
    def test_trailing_fragment(self) -> None:
        s = SentenceStreamer()
        assert s.push_chunk("This is incomplete") == []
        assert s.flush() == ["This is incomplete"]


class TestIncrementalEquivalence:
    def test_token_boundaries_do_not_change_result(self) -> None:
        text = (
            "See i.e. the note from Mr. Smith vs. the other copy. "
            "Pi is 3.14 and version 2.0 is live.\nNext line is also a sentence."
        )
        assert _push_incremental(text) == _push_all(text)


class TestLatency:
    def test_first_sentence_under_50ms_after_boundary(self) -> None:
        s = SentenceStreamer(min_sentence_chars=20)
        s.push_chunk("This is a complete sentence")
        t0 = time.perf_counter()
        out = s.push_chunk(".")
        elapsed_ms = (time.perf_counter() - t0) * 1000
        assert out == ["This is a complete sentence."]
        assert elapsed_ms < 50
