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


def test_telegram_generic_website_shortcut_does_not_replace_each_post_url():
    from aggregator.collector import parse_telegram_page
    source = {"name": "New Kaliningrad", "feed_url": "https://t.me/s/newkal_stream",
              "homepage": "https://www.newkaliningrad.ru/", "enabled": True}
    html = '''<div class="tgme_widget_message" data-post="newkal_stream/111">
       <div class="tgme_widget_message_text">В Калининграде объявили воздушную тревогу.
       <a href="https://newkaliningrad.ru/t">Наш канал</a></div>
       <a class="tgme_widget_message_date"><time datetime="2026-11-21T19:00:00+00:00"></time></a></div>
       <div class="tgme_widget_message" data-post="newkal_stream/112">
       <div class="tgme_widget_message_text">В Балтийске объявили воздушную тревогу.
       <a href="https://newkaliningrad.ru/t">Наш канал</a>
       <a href="https://newkaliningrad.ru/news/123.html">Подробнее</a></div>
       <a class="tgme_widget_message_date"><time datetime="2026-11-21T19:01:00+00:00"></time></a></div>'''
    posts = parse_telegram_page(html, source)
    assert posts[0].url == 'https://newkaliningrad.ru/news/123.html'
    assert posts[1].url == 'https://t.me/newkal_stream/111'
