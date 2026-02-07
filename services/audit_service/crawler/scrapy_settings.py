from services.audit_service.config import settings

SCRAPY_SETTINGS = {
    "ROBOTSTXT_OBEY": False,
    "USER_AGENT": settings.user_agent,
    "DOWNLOAD_TIMEOUT": int(settings.default_timeout_s),
    "LOG_LEVEL": "WARNING",
    "TELNETCONSOLE_ENABLED": False,
    "RETRY_ENABLED": True,
    "RETRY_TIMES": 2,
    "RETRY_HTTP_CODES": [429, 500, 502, 503, 504],
    "DUPEFILTER_CLASS": "scrapy.dupefilters.RFPDupeFilter",
    "TWISTED_REACTOR": "twisted.internet.asyncioreactor.AsyncioSelectorReactor",
}