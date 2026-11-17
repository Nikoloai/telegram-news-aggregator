from aggregator.article import ArticleFetcher, article_image_url
from aggregator.collector import parse_feed, parse_telegram_page
from aggregator.models import FeedItem
from bs4 import BeautifulSoup


def test_article_og_image_is_used():
    html = '<html><head><meta property="og:image" content="/images/lead.jpg"></head><body></body></html>'
    assert article_image_url(BeautifulSoup(html, "html.parser"),
                             "https://publisher.example/story") == "https://publisher.example/images/lead.jpg"


def test_insecure_og_image_is_rejected():
    html = '<html><head><meta property="og:image" content="http://publisher.example/lead.jpg"></head></html>'
    assert article_image_url(BeautifulSoup(html, "html.parser"),
                             "https://publisher.example/story") is None


def test_rss_media_image_is_preserved():
    xml = """<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/">
      <channel><title>Publisher</title><item>
       <title>Новость</title><link>https://publisher.example/1</link>
       <media:content url="https://publisher.example/one.jpg" type="image/jpeg"/>
      </item></channel></rss>"""
    posts = parse_feed(xml, "Publisher")
    assert posts[0].image_url == "https://publisher.example/one.jpg"


def test_telegram_preview_photo_is_extracted():
    html = """<div class="tgme_widget_message" data-post="test_channel/111">
      <div class="tgme_widget_message_text">Калининград: важные новости города и области сегодня.</div>
      <a class="tgme_widget_message_photo_wrap"
          style="background-image:url('https://cdn.example.org/news.jpg')"></a>
      <a class="tgme_widget_message_date"><time datetime="2026-11-16T12:00:00+00:00"></time></a>
    </div>"""
    source = {"name": "Test", "feed_url": "https://t.me/s/test_channel"}
    posts = parse_telegram_page(html, source)
    assert len(posts) == 1
    assert posts[0].image_url == "https://cdn.example.org/news.jpg"


def test_telegram_source_photo_survives_no_article_fetch():
    item = FeedItem("Test", "Title", "https://t.me/test_channel/111",
                    description="A detailed account from the source is here.",
                    image_url="https://cdn.example.org/news.jpg")
    article = ArticleFetcher().fetch(item)
    assert article.image_url == item.image_url
