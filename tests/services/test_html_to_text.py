from glintory.services.html_to_text import html_to_plain_text


def test_html_to_plain_text_basic():
    # tag除去
    assert (
        html_to_plain_text("<p>Hello <b>World</b></p>", max_chars=100) == "Hello World"
    )


def test_html_to_plain_text_entity_decode():
    # entity decode
    assert (
        html_to_plain_text("Hello &amp; World &lt; &gt; &quot;", max_chars=100)
        == 'Hello & World < > "'
    )


def test_html_to_plain_text_boundaries():
    # paragraph/br/li境界
    assert (
        html_to_plain_text("<p>First</p><p>Second</p>", max_chars=100) == "First Second"
    )
    assert html_to_plain_text("Line1<br>Line2", max_chars=100) == "Line1 Line2"
    assert (
        html_to_plain_text("<ul><li>Item1</li><li>Item2</li></ul>", max_chars=100)
        == "Item1 Item2"
    )


def test_html_to_plain_text_script_style_exclude():
    # script/style除外
    html_input = "<div>Show this <script>console.log('hide this');</script> and <style>body { color: red; }</style> show this too</div>"
    assert (
        html_to_plain_text(html_input, max_chars=100) == "Show this and show this too"
    )


def test_html_to_plain_text_nul_char():
    # NUL除去
    assert html_to_plain_text("Hello\0World", max_chars=100) == "HelloWorld"


def test_html_to_plain_text_whitespace_normalization():
    # whitespace正規化
    assert (
        html_to_plain_text("  Hello   \n \t  World  ", max_chars=100) == "Hello World"
    )


def test_html_to_plain_text_empty_and_none():
    # empty input
    assert html_to_plain_text(None, max_chars=100) == ""
    assert html_to_plain_text("", max_chars=100) == ""


def test_html_to_plain_text_malformed():
    # malformed HTML
    assert html_to_plain_text("<p>Unclosed tag", max_chars=100) == "Unclosed tag"
    assert (
        html_to_plain_text("Hello <p borken attr>World", max_chars=100) == "Hello World"
    )


def test_html_to_plain_text_max_chars():
    # max chars
    assert html_to_plain_text("<p>abcdefghij</p>", max_chars=5) == "abcde"


def test_html_to_plain_text_no_execution():
    # HTMLを実行しない（単にパースしてテキストを返すことを確認）
    assert html_to_plain_text("<script>alert(1)</script>", max_chars=10) == ""
