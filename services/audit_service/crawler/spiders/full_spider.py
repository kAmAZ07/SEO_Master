import scrapy


class FullSpider(scrapy.Spider):
    name = "full_spider"

    def __init__(self, root_url: str, max_pages: int = 10000, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.root_url = root_url
        self.max_pages = int(max_pages)
        self.seen = set()
        self.count = 0

    def start_requests(self):
        yield scrapy.Request(self.root_url, callback=self.parse, dont_filter=True)

    def parse(self, response):
        if self.count >= self.max_pages:
            return
        self.count += 1
        url = str(response.url)
        self.seen.add(url)
        yield {
            "url": url,
            "status": int(response.status),
            "html": response.text,
            "headers": {k.decode(): [v.decode() for v in vals] for k, vals in response.headers.items()},
        }
        for href in response.css("a::attr(href)").getall():
            if self.count >= self.max_pages:
                break
            next_url = response.urljoin(href)
            if next_url not in self.seen:
                yield scrapy.Request(next_url, callback=self.parse)