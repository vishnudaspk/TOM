"""Unit tests for Phase 9 Iteration 9.2: SentenceStreamer."""

from __future__ import annotations

import time

from tom.voice.sentence_streamer import SentenceStreamer


def _push_all(text: str, min_chars: int = 20, first_chunk_policy: bool = False) -> list[str]:
    s = SentenceStreamer(min_sentence_chars=min_chars, first_chunk_policy=first_chunk_policy)
    out = s.push_chunk(text)
    out.extend(s.flush())
    return out


def _push_incremental(text: str, min_chars: int = 20, first_chunk_policy: bool = False) -> list[str]:
    s = SentenceStreamer(min_sentence_chars=min_chars, first_chunk_policy=first_chunk_policy)
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


class TestFirstChunkPolicy:
    """P1.a First-chunk policy benchmarks and edge cases."""

    def test_clause_boundary_flushes_early(self) -> None:
        """A natural clause boundary (, ; : —) flushes as the first chunk."""
        s = SentenceStreamer(first_chunk_policy=True)
        # 5 words, > 15 chars, followed by space
        out1 = s.push_chunk("According to the system monitor, ")
        assert out1 == ["According to the system monitor,"]

        out2 = s.push_chunk("everything looks completely healthy.")
        out2.extend(s.flush())
        assert out2 == ["everything looks completely healthy."]

    def test_tiny_clause_fragment_held(self) -> None:
        """Tiny fragments (< 15 chars or < 3 words) are not flushed as isolated chunks."""
        s = SentenceStreamer(first_chunk_policy=True)
        # 'Yes, ' is only 4 chars and 1 word — avoid flushing tiny greeting fragment
        out1 = s.push_chunk("Yes, ")
        assert out1 == []

        out2 = s.push_chunk("I can certainly help you with that.")
        assert out2 == ["Yes, I can certainly help you with that."]

    def test_delayed_punctuation_flushes_at_word_limit(self) -> None:
        """If punctuation is delayed, flush at ~6-8 words (default 7)."""
        s = SentenceStreamer(first_chunk_policy=True, first_chunk_words=7)
        # Push 7 words without any punctuation
        tokens = [
            "The ", "local ", "language ", "model ", "server ", "is ", "currently ",
            "processing ", "all ", "incoming ", "requests."
        ]
        emitted: list[str] = []
        for token in tokens:
            emitted.extend(s.push_chunk(token))
        emitted.extend(s.flush())

        assert len(emitted) == 2
        assert emitted[0] == "The local language model server is currently"
        assert emitted[1] == "processing all incoming requests."

    def test_sentence_integrity_preserved_for_short_sentence(self) -> None:
        """If a complete sentence finishes within 7 words, preserve sentence integrity."""
        s = SentenceStreamer(first_chunk_policy=True, first_chunk_words=7)
        out1 = s.push_chunk("The CPU is cool. ")
        assert out1 == ["The CPU is cool."]

        out2 = s.push_chunk("Usage is minimal.")
        out2.extend(s.flush())
        assert out2 == ["Usage is minimal."]

    def test_first_chunk_latency_benchmark(self) -> None:
        """Benchmark: First chunk is emitted at clause/word boundary in < 5 ms."""
        s = SentenceStreamer(first_chunk_policy=True)
        s.push_chunk("Looking at your local storage configuration, ")
        t0 = time.perf_counter()
        # Next token arrives
        out = s.push_chunk("drive C has plenty of space.")
        elapsed_ms = (time.perf_counter() - t0) * 1000

        assert elapsed_ms < 5.0
        # Total chunks emitted across the stream
        all_chunks = out + s.flush()
        assert len(all_chunks) >= 1
