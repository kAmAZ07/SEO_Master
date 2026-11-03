"""Проверка robots.txt: чтение через сетевую политику, правила применяются к каждому URL."""
from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

from shared.netpolicy import NetPolicyError

from services.audit_service.config import settings
from services.audit_service.netio import AuditFetcher, BudgetExhausted
from services.audit_service.public_policy import ROBOTS_MAX_BYTES


def _robots_url(root_url: str) -> str:
    p = urlparse(root_url)
    return f"{p.scheme}://{p.netloc}/robots.txt"


def _blocked_root(robots_text: str) -> bool:
    """Закрыт ли корень для всех роботов (User-agent: * и Disallow: /)."""
    ua_any = False
    disallows: list[str] = []
    for raw in robots_text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        lowered = line.lower()
        if lowered.startswith("user-agent:"):
            ua_any = line.split(":", 1)[1].strip() == "*"
        elif ua_any and lowered.startswith("disallow:"):
            disallows.append(line.split(":", 1)[1].strip())
    return "/" in disallows


@dataclass
class RobotsInfo:
    """Результат чтения robots.txt.

    state: ok — файл прочитан; missing — 4xx (ограничений нет); unreachable — 5xx, таймаут
    или отказ сети. По RFC 9309 недоступный robots.txt считается полным запретом.
    """

    robots_url: str
    state: str
    status_code: int | None = None
    error: str | None = None
    sitemaps: list[str] = field(default_factory=list)
    _parser: RobotFileParser | None = None
    _blocked_root: bool = False

    @property
    def disallow_all(self) -> bool:
        return self.state == "unreachable" or self._blocked_root

    def can_fetch(self, url: str) -> bool:
        if self.state == "unreachable":
            return False
        if self._parser is None:
            return True
        return self._parser.can_fetch(settings.user_agent, url)

    def to_summary(self) -> dict:
        return {
            "robots_url": self.robots_url,
            "available": self.state == "ok",
            "state": self.state,
            "blocked_root": self.disallow_all,
            "status_code": self.status_code,
            "error": self.error,
        }


async def fetch_robots(root_url: str, fetcher: AuditFetcher) -> RobotsInfo:
    url = _robots_url(root_url)
    try:
        response = await fetcher.fetch(url, kind="aux", max_body_bytes=ROBOTS_MAX_BYTES)
    except BudgetExhausted:
        raise
    except NetPolicyError as exc:
        return RobotsInfo(robots_url=url, state="unreachable", error=exc.code)

    if 400 <= response.status < 500:
        return RobotsInfo(robots_url=url, state="missing", status_code=response.status)
    if response.status >= 500 or response.status >= 300:
        return RobotsInfo(robots_url=url, state="unreachable", status_code=response.status, error=f"http_{response.status}")

    text = response.body.decode("utf-8", errors="replace")
    parser = RobotFileParser()
    parser.parse(text.splitlines())
    sitemaps = [
        line.split(":", 1)[1].strip()
        for line in (raw.split("#", 1)[0].strip() for raw in text.splitlines())
        if line.lower().startswith("sitemap:") and line.split(":", 1)[1].strip()
    ]
    return RobotsInfo(
        robots_url=url,
        state="ok",
        status_code=response.status,
        sitemaps=sitemaps[:10],
        _parser=parser,
        _blocked_root=_blocked_root(text),
    )


async def check_robots(root_url: str, fetcher: AuditFetcher | None = None) -> dict:
    """Совместимая обёртка: возвращает словарь для summary."""
    fetcher = fetcher or AuditFetcher.create()
    return (await fetch_robots(root_url, fetcher)).to_summary()
