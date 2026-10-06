from aggregator.collector import parse_feed


RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Test</title>
  <item>
    <title>Суд арестовал журналиста</title>
    <link>https://example.org/news/1</link>
    <description><![CDATA[<p>Описание новости</p>]]></description>
    <category>Политика</category>
    <pubDate>Mon, 06 Oct 2025 12:00:00 GMT</pubDate>
  </item>
</channel></rss>""".encode("utf-8")


def test_parse_rss() -> None:
    items = parse_feed(RSS, "Тест")
    assert len(items) == 1
    assert items[0].source == "Тест"
    assert items[0].title == "Суд арестовал журналиста"
    assert items[0].url == "https://example.org/news/1"
    assert items[0].categories == ["Политика"]
    assert items[0].published_at is not None
