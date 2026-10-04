from app.parsers.html import _getlinkedpages

HTML = (
    '<a href="/a">1</a><a href="b/c">2</a><a href="//cdn.other.com/x">3</a>'
    '<a href="https://notexample.com/z">4</a><a href="https://blog.example.com/p#f">5</a>'
    '<a href="/a">6</a><a href="/login">7</a><a href="/img.png">8</a>'
)


def test_linked_pages_resolve_and_filter():
    links = _getlinkedpages(HTML, "example.com", "https://www.example.com/news/")
    assert links == [
        "https://www.example.com/a",
        "https://www.example.com/news/b/c",
        "https://blog.example.com/p",
    ]
