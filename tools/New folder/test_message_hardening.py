from __future__ import annotations

from message_html import count_message_html_words


def test_word_count_uses_visible_html_text_only() -> None:
    html = (
        "<style>.hidden { color: red; }</style>"
        "<p>One, <strong>two three</strong> four.</p>"
        "<script>five six seven</script>"
    )

    assert count_message_html_words(html) == 4
