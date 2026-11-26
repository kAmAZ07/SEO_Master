"""Проверка robots.txt: чтение через сетевую политику, правила применяются к каждому URL."""
from __future__ import annotations

from dataclasses import dataclass, field
import re
from urllib.parse import unquote, urljoin, urlparse

from shared.netpolicy import NetPolicyError
from shared.projects.scope import ScopeError, scope_from_url

from services.audit_service.config import settings
from services.audit_service.netio import AuditFetcher, BudgetExhausted
from services.audit_service.public_policy import ROBOTS_MAX_BYTES


def _robots_url(root_url: str) -> str:
    p = urlparse(root_url)
    return f"{p.scheme}://{p.netloc}/robots.txt"


def _literal_rules(lines: list[str], user_agent: str) -> dict[str, bool]:
    groups: list[tuple[set[str], list[tuple[str, bool]]]] = []
    agents: set[str] = set()
    directives: list[tuple[str, bool]] = []
    for line in lines:
        name, _, value = line.partition(":")
        name, value = name.strip(" \t").lower(), value.strip(" \t")
        if name == "user-agent":
            if directives:
                groups.append((agents, directives))
                agents, directives = set(), []
            if not re.fullmatch(r"\*|[a-zA-Z_-]+", value):
                raise ValueError("robots_rules_unsupported")
            agents.add(value.lower())
        elif agents and name in ("allow", "disallow"):
            directives.append((value, name == "allow"))
    groups.append((agents, directives))

    product = user_agent.split("/", 1)[0].strip().lower()
    selector = product if any(product in group for group, _ in groups) else "*"
    rules: dict[str, bool] = {}
    for group, directives in groups:
        if selector not in group:
            continue
        for pattern, allow in directives:
            if not pattern:
                continue
            if not re.fullmatch(r"/[A-Za-z0-9._~/-]*", pattern):
                raise ValueError("robots_rules_unsupported")
            rules[pattern] = rules.get(pattern, False) or allow
    return rules


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
    _rules: dict[str, bool] = field(default_factory=dict)

    def _allows(self, path: str) -> bool:
        for end in range(len(path), 0, -1):
            verdict = self._rules.get(path[:end])
            if verdict is not None:
                return verdict
        return True

    def can_fetch(self, url: str) -> bool:
        if self.state == "unreachable":
            return False
        if not self._rules:
            return True
        try:
            raw_path = unquote(urlparse(url).path or "/", errors="strict")
            normalized_path = unquote(scope_from_url(url).path_prefix, errors="strict")
        except (ScopeError, UnicodeDecodeError):
            return False
        if normalized_path != "/" and raw_path.endswith("/"):
            normalized_path += "/"
        return self._allows(raw_path) and self._allows(normalized_path)

    def to_summary(self) -> dict:
        return {
            "robots_url": self.robots_url,
            "available": self.state == "ok",
            "state": self.state,
            "blocked_root": not self.can_fetch(urljoin(self.robots_url, "/")),
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

    try:
        text = response.body.decode("utf-8-sig", errors="strict")
        physical_lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        lines = [raw.split("#", 1)[0].strip(" \t") for raw in physical_lines]
        if any(re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\u0085\u2028\u2029]", line) for line in lines):
            raise ValueError("robots_rules_unsupported")
        rules = _literal_rules(lines, settings.user_agent)
    except ValueError:
        return RobotsInfo(robots_url=url, state="unreachable", status_code=response.status, error="robots_rules_unsupported")
    sitemaps = [
        line.split(":", 1)[1].strip(" \t")
        for line in lines
        if line.lower().startswith("sitemap:") and line.split(":", 1)[1].strip(" \t")
    ]
    return RobotsInfo(
        robots_url=url,
        state="ok",
        status_code=response.status,
        sitemaps=sitemaps[:10],
        _rules=rules,
    )


async def check_robots(root_url: str, fetcher: AuditFetcher | None = None) -> dict:
    """Совместимая обёртка: возвращает словарь для summary."""
    fetcher = fetcher or AuditFetcher.create()
    return (await fetch_robots(root_url, fetcher)).to_summary()
