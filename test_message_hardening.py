from __future__ import annotations

from message_html import count_message_html_words, truncate_message_html_words


def test_word_count_uses_visible_html_text_only() -> None:
    html = (
        "<style>.hidden { color: red; }</style>"
        "<p>One, <strong>two three</strong> four.</p>"
        "<script>five six seven</script>"
    )

    assert count_message_html_words(html) == 4


def test_truncation_preserves_formatting_and_valid_closing_tags() -> None:
    html = (
        '<p class="opening">One, <strong>two three</strong> four '
        "<em>five six</em>.</p><ul><li>seven eight</li></ul>"
    )

    truncated = truncate_message_html_words(html, 4)

    assert count_message_html_words(truncated) == 4
    assert '<p class="opening">' in truncated
    assert "<strong>two three</strong>" in truncated
    assert truncated.endswith("</em></p>")
    assert "five" not in truncated
