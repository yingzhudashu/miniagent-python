"""Tests for miniagent/engine/utils.py."""

import pytest

from miniagent.engine.utils import (
    detect_ext_from_magic,
    detect_mime_from_magic,
    format_duration_seconds,
    format_file_size,
    truncate_text,
    extract_last_qa_from_history,
    get_terminal_width,
    get_render_width,
    MIN_RENDER_WIDTH,
    MAX_RENDER_WIDTH,
)


class TestTerminalWidth:
    """Tests for terminal width functions."""

    def test_get_terminal_width_fallback(self):
        """Fallback value when terminal size unavailable."""
        # In test environment, should return fallback
        width = get_terminal_width(fallback_width=100)
        assert width >= 1

    def test_get_render_width_bounds(self):
        """Render width respects min/max bounds."""
        width = get_render_width(fallback_width=80)
        assert width >= MIN_RENDER_WIDTH
        assert width <= MAX_RENDER_WIDTH

    def test_get_render_width_small_terminal(self):
        """Small terminal returns minimum width."""
        # Even with tiny fallback, should return minimum
        width = get_render_width(fallback_width=20)
        assert width == MIN_RENDER_WIDTH

    def test_get_render_width_large_terminal(self):
        """Large terminal returns capped width."""
        width = get_render_width(fallback_width=1000)
        assert width == MAX_RENDER_WIDTH


class TestFormatDuration:
    """Tests for format_duration_seconds."""

    def test_seconds(self):
        """Format seconds."""
        assert format_duration_seconds(5.5) == "5.5s"
        assert format_duration_seconds(45.2) == "45.2s"

    def test_minutes(self):
        """Format minutes and seconds."""
        assert format_duration_seconds(90) == "1m30s"
        assert format_duration_seconds(150) == "2m30s"

    def test_hours(self):
        """Format hours and minutes."""
        assert format_duration_seconds(3661) == "1h1m"
        assert format_duration_seconds(7200) == "2h0m"


class TestFormatFileSize:
    """Tests for format_file_size."""

    def test_bytes(self):
        """Format bytes."""
        assert format_file_size(100) == "100B"
        assert format_file_size(1023) == "1023B"

    def test_kilobytes(self):
        """Format kilobytes."""
        assert format_file_size(1024) == "1KB"
        assert format_file_size(2048) == "2KB"
        assert format_file_size(1536) == "1KB"

    def test_megabytes(self):
        """Format megabytes."""
        assert format_file_size(1024 * 1024) == "1.0MB"
        assert format_file_size(2.5 * 1024 * 1024) == "2.5MB"

    def test_gigabytes(self):
        """Format gigabytes."""
        assert format_file_size(1024 * 1024 * 1024) == "1.00GB"
        assert format_file_size(2.5 * 1024 * 1024 * 1024) == "2.50GB"


class TestTruncateText:
    """Tests for truncate_text."""

    def test_no_truncate(self):
        """Text shorter than max_length unchanged."""
        assert truncate_text("short", 10) == "short"

    def test_truncate_with_suffix(self):
        """Text longer than max_length truncated."""
        assert truncate_text("long text here", 10) == "long te..."

    def test_custom_suffix(self):
        """Custom suffix works."""
        assert truncate_text("long text", 6, suffix="…") == "long …"

    def test_exact_length(self):
        """Text exactly at max_length unchanged."""
        assert truncate_text("exact", 5) == "exact"


class TestMagicDetection:
    """Tests for magic bytes detection."""

    def test_png_detection(self):
        """Detect PNG magic bytes."""
        data = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
        assert detect_ext_from_magic(data) == ".png"
        assert detect_mime_from_magic(data) == "image/png"

    def test_jpg_detection(self):
        """Detect JPEG magic bytes."""
        data = b"\xff\xd8\xff\xe0\x00\x10JFIF"
        assert detect_ext_from_magic(data) == ".jpg"
        assert detect_mime_from_magic(data) == "image/jpeg"

    def test_gif_detection(self):
        """Detect GIF magic bytes."""
        data = b"GIF89a\x00\x00"
        assert detect_ext_from_magic(data) == ".gif"
        assert detect_mime_from_magic(data) == "image/gif"

    def test_webp_detection(self):
        """Detect WebP magic bytes."""
        data = b"RIFF\x00\x00\x00\x00WEBP"
        assert detect_ext_from_magic(data) == ".webp"
        assert detect_mime_from_magic(data) == "image/webp"

    def test_mp4_detection(self):
        """Detect MP4 magic bytes."""
        data = b"\x00\x00\x00\x1cftypisom"
        assert detect_ext_from_magic(data) == ".mp4"
        assert detect_mime_from_magic(data) == "video/mp4"

    def test_zip_detection(self):
        """Detect ZIP magic bytes (also DOCX/XLSX)."""
        data = b"PK\x03\x04\x00\x00\x00"
        assert detect_ext_from_magic(data) == ".zip"
        assert detect_mime_from_magic(data) == "application/zip"

    def test_pdf_detection(self):
        """Detect PDF magic bytes."""
        data = b"%PDF-1.4\x00"
        assert detect_ext_from_magic(data) == ".pdf"
        assert detect_mime_from_magic(data) == "application/pdf"

    def test_unknown_detection(self):
        """Unknown data returns None."""
        data = b"\x00\x00\x00\x00unknown"
        assert detect_ext_from_magic(data) is None
        assert detect_mime_from_magic(data) is None

    def test_empty_data(self):
        """Empty data returns None."""
        assert detect_ext_from_magic(b"") is None
        assert detect_mime_from_magic(b"") is None

    def test_partial_magic(self):
        """Partial magic match returns None."""
        data = b"\x89PN"  # Incomplete PNG magic
        assert detect_ext_from_magic(data) is None


class TestExtractLastQA:
    """Tests for extract_last_qa_from_history."""

    def test_basic_extraction(self):
        """Extract last user/assistant pair."""
        history = [
            {"role": "user", "content": "First question"},
            {"role": "assistant", "content": "First answer"},
            {"role": "user", "content": "Second question"},
            {"role": "assistant", "content": "Second answer"},
        ]
        result = extract_last_qa_from_history(history)
        assert result == ("Second question", "Second answer")

    def test_missing_assistant(self):
        """Return None if no assistant message."""
        history = [
            {"role": "user", "content": "Question"},
        ]
        assert extract_last_qa_from_history(history) is None

    def test_missing_user(self):
        """Return None if no user message after assistant."""
        history = [
            {"role": "assistant", "content": "Answer"},
        ]
        assert extract_last_qa_from_history(history) is None

    def test_empty_history(self):
        """Return None for empty history."""
        assert extract_last_qa_from_history([]) is None

    def test_mixed_roles(self):
        """Extract correctly with system/tool messages."""
        history = [
            {"role": "system", "content": "System prompt"},
            {"role": "user", "content": "Q1"},
            {"role": "assistant", "content": "A1"},
            {"role": "tool", "content": "Tool output"},
            {"role": "user", "content": "Q2"},
            {"role": "assistant", "content": "A2"},
        ]
        result = extract_last_qa_from_history(history)
        assert result == ("Q2", "A2")