"""Unit tests for Phase 6 Iteration 2: SpeechFormatter.

All tests are deterministic and offline.
Tests markdown removal, whitespace normalization, symbol expansion,
and speech-friendly formatting of LLM outputs.
"""

from __future__ import annotations

from tom.voice.formatter import SpeechFormatter


class TestSpeechFormatterHeadings:
    def test_single_hash_heading(self) -> None:
        formatter = SpeechFormatter()
        result = formatter.format("# Welcome to TOM")
        assert result == "Welcome to TOM."

    def test_multi_level_headings(self) -> None:
        formatter = SpeechFormatter()
        text = "## Architecture Overview\n### System Details"
        result = formatter.format(text)
        assert result == "Architecture Overview. System Details."

    def test_heading_with_existing_punctuation(self) -> None:
        formatter = SpeechFormatter()
        result = formatter.format("# What is TOM?")
        assert result == "What is TOM?"

    def test_heading_with_exclamation(self) -> None:
        formatter = SpeechFormatter()
        result = formatter.format("# Hello World!")
        assert result == "Hello World!"


class TestSpeechFormatterBoldItalic:
    def test_double_asterisk_bold(self) -> None:
        formatter = SpeechFormatter()
        result = formatter.format("This is **important** information.")
        assert result == "This is important information."

    def test_double_underscore_bold(self) -> None:
        formatter = SpeechFormatter()
        result = formatter.format("This is __critical__ data.")
        assert result == "This is critical data."

    def test_single_asterisk_italic(self) -> None:
        formatter = SpeechFormatter()
        result = formatter.format("Please note *carefully* before proceeding.")
        assert result == "Please note carefully before proceeding."

    def test_single_underscore_italic(self) -> None:
        formatter = SpeechFormatter()
        result = formatter.format("This is _emphasized_ text.")
        assert result == "This is emphasized text."

    def test_triple_asterisk_bold_italic(self) -> None:
        formatter = SpeechFormatter()
        result = formatter.format("This is ***very important***.")
        assert result == "This is very important."

    def test_snake_case_preserved(self) -> None:
        formatter = SpeechFormatter()
        result = formatter.format("Check my_variable_name and another_function_call.")
        assert result == "Check my_variable_name and another_function_call."

    def test_strikethrough(self) -> None:
        formatter = SpeechFormatter()
        result = formatter.format("This was ~~deprecated~~ removed.")
        assert result == "This was deprecated removed."


class TestSpeechFormatterLinksAndImages:
    def test_markdown_link(self) -> None:
        formatter = SpeechFormatter()
        result = formatter.format("Visit [OpenAI](https://openai.com) for details.")
        assert result == "Visit OpenAI for details."

    def test_multiple_links(self) -> None:
        formatter = SpeechFormatter()
        result = formatter.format(
            "Compare [Python](https://python.org) and [Rust](https://rust-lang.org)."
        )
        assert result == "Compare Python and Rust."

    def test_reference_link(self) -> None:
        formatter = SpeechFormatter()
        result = formatter.format("See [the documentation][doc-ref] for more.")
        assert result == "See the documentation for more."

    def test_markdown_image(self) -> None:
        formatter = SpeechFormatter()
        result = formatter.format("Diagram: ![Architecture Flow](https://example.com/arch.png)")
        assert result == "Diagram: Architecture Flow"

    def test_empty_alt_image(self) -> None:
        formatter = SpeechFormatter()
        result = formatter.format("Look here: ![](https://example.com/img.png)")
        assert result == "Look here:"


class TestSpeechFormatterCode:
    def test_inline_code(self) -> None:
        formatter = SpeechFormatter()
        result = formatter.format("Run `pip install kokoro-onnx` in terminal.")
        assert result == "Run pip install kokoro-onnx in terminal."

    def test_fenced_code_delimiters_stripped_by_default(self) -> None:
        formatter = SpeechFormatter()
        text = "Here is the command:\n```bash\ncargo build --release\n```\nAll done."
        result = formatter.format(text)
        assert "```" not in result
        assert "cargo build --release" in result
        assert "All done." in result

    def test_fenced_code_block_dropped_when_configured(self) -> None:
        formatter = SpeechFormatter(strip_code_blocks=True)
        text = "Here is the code:\n```python\ndef add(a, b):\n    return a + b\n```\nLet me know."
        result = formatter.format(text)
        assert "def add" not in result
        assert "Here is the code:" in result
        assert "Let me know." in result

    def test_fenced_code_replacement(self) -> None:
        formatter = SpeechFormatter(strip_code_blocks=True, code_replacement="[code snippet]")
        text = "Review this:\n```python\nx = 1\n```\nLooks good."
        result = formatter.format(text)
        assert "[code snippet]" in result


class TestSpeechFormatterLists:
    def test_bullet_list_hyphen(self) -> None:
        formatter = SpeechFormatter()
        text = "- First item\n- Second item\n- Third item"
        result = formatter.format(text)
        assert "First item." in result
        assert "Second item." in result
        assert "Third item." in result

    def test_bullet_list_asterisk(self) -> None:
        formatter = SpeechFormatter()
        text = "* Apple\n* Banana\n* Orange"
        result = formatter.format(text)
        assert "Apple." in result
        assert "Banana." in result
        assert "Orange." in result

    def test_numbered_list(self) -> None:
        formatter = SpeechFormatter()
        text = "1. Open the door\n2. Walk outside\n3. Breathe fresh air"
        result = formatter.format(text)
        assert "Open the door." in result
        assert "Walk outside." in result
        assert "Breathe fresh air." in result

    def test_mixed_list_with_prose(self) -> None:
        formatter = SpeechFormatter()
        text = "Three steps:\n1. First\n2. Second\nThat is all."
        result = formatter.format(text)
        assert "Three steps:" in result
        assert "First." in result
        assert "Second." in result
        assert "That is all." in result


class TestSpeechFormatterBlockquotesAndDividers:
    def test_blockquote(self) -> None:
        formatter = SpeechFormatter()
        result = formatter.format("> Remember that local AI runs offline.")
        assert result == "Remember that local AI runs offline."

    def test_horizontal_rule(self) -> None:
        formatter = SpeechFormatter()
        text = "Section one.\n---\nSection two."
        result = formatter.format(text)
        assert "---" not in result
        assert "Section one. Section two." in result


class TestSpeechFormatterTables:
    def test_markdown_table_cleaned(self) -> None:
        formatter = SpeechFormatter()
        text = "| Component | Status |\n|---|---|\n| STT | Complete |\n| TTS | Current |\n"
        result = formatter.format(text)
        assert "---" not in result
        assert "|" not in result
        assert "STT" in result
        assert "Complete" in result
        assert "TTS" in result
        assert "Current" in result


class TestSpeechFormatterWhitespaceAndPunctuation:
    def test_empty_string(self) -> None:
        formatter = SpeechFormatter()
        assert formatter.format("") == ""

    def test_whitespace_only(self) -> None:
        formatter = SpeechFormatter()
        assert formatter.format("   \n\t   \n  ") == ""

    def test_excessive_spaces_collapsed(self) -> None:
        formatter = SpeechFormatter()
        result = formatter.format("Word1    Word2     Word3")
        assert result == "Word1 Word2 Word3"

    def test_excessive_newlines_collapsed(self) -> None:
        formatter = SpeechFormatter()
        result = formatter.format("Paragraph one.\n\n\n\nParagraph two.")
        assert result == "Paragraph one. Paragraph two."

    def test_space_before_punctuation_cleaned(self) -> None:
        formatter = SpeechFormatter()
        result = formatter.format("Hello , world ! How are you ?")
        assert result == "Hello, world! How are you?"

    def test_prose_and_natural_punctuation_preserved(self) -> None:
        formatter = SpeechFormatter()
        text = "Hello, world! It's a nice day; isn't it? Yes - absolutely."
        result = formatter.format(text)
        assert result == text


class TestSpeechFormatterSymbolExpansions:
    def test_percent_expansion(self) -> None:
        formatter = SpeechFormatter()
        result = formatter.format("Accuracy is 95% today.")
        assert result == "Accuracy is 95 percent today."

    def test_ampersand_expansion(self) -> None:
        formatter = SpeechFormatter()
        result = formatter.format("Tom & Jerry went home.")
        assert result == "Tom and Jerry went home."

    def test_abbreviations(self) -> None:
        formatter = SpeechFormatter()
        result = formatter.format("Coffee w/ sugar, w/o milk.")
        assert result == "Coffee with sugar, without milk."


class TestSpeechFormatterTruncation:
    def test_no_truncation_when_below_limit(self) -> None:
        formatter = SpeechFormatter(max_chars=100)
        text = "This is a short sentence."
        assert formatter.format(text) == text

    def test_sentence_boundary_truncation(self) -> None:
        formatter = SpeechFormatter(max_chars=35)
        text = "First complete sentence. Second complete sentence. Third sentence."
        result = formatter.format(text)
        assert result == "First complete sentence."

        # At max_chars=55, both first and second sentences fit
        formatter2 = SpeechFormatter(max_chars=55)
        result2 = formatter2.format(text)
        assert result2 == "First complete sentence. Second complete sentence."

    def test_word_boundary_fallback(self) -> None:
        formatter = SpeechFormatter(max_chars=30)
        text = "This is a long sentence without any early periods."
        result = formatter.format(text)
        assert len(result) <= 33  # word + ellipsis
        assert result.endswith("...")
