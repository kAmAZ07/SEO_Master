"""Сетевой слой аудита: все запросы к сайту пользователя идут через SafeHttpClient.

`Budget` ограничивает время и число запросов одного задания, `AuditFetcher` применяет
политику (SSRF, редиректы, размеры) и учитывает бюджет. Основные запросы (HTML-страницы)
и вспомогательные (robots, sitemap, HEAD-проверки) считаются отдельно.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, replace
from typing import Optional

from shared.netpolicy import FetchResult, NetPolicy, NetPolicyError, SafeHttpClient, parse_target, resolve_public

from services.audit_service.config import settings
from services.audit_service.public_policy import (
    HTML_MAX_BYTES,
    PUBLIC_CONNECT_TIMEOUT_S,
    PUBLIC_MAX_AUX_REQUESTS,
    PUBLIC_MAX_PAGES,
    PUBLIC_MAX_REDIRECTS,
    PUBLIC_READ_TIMEOUT_S,
)


class BudgetExhausted(NetPolicyError):
    """Бюджет времени или запросов задания исчерпан. Не отказ политики безопасности."""

    def __init__(self, reason: str):
        super().__init__("budget_exhausted", reason)
        self.reason = reason


class Budget:
    def __init__(
        self,
        *,
        deadline_s: Optional[float] = None,
        max_page_requests: int = 2 * PUBLIC_MAX_PAGES,  # с запасом на не-HTML и редиректы по ссылкам
        max_aux_requests: int = PUBLIC_MAX_AUX_REQUESTS,
        clock=time.monotonic,
    ):
        self._clock = clock
        self._deadline = None if deadline_s is None else clock() + deadline_s
        self.max_page_requests = max_page_requests
        self.max_aux_requests = max_aux_requests
        self.page_requests = 0
        self.aux_requests = 0

    def remaining(self) -> float:
        if self._deadline is None:
            return float("inf")
        return max(0.0, self._deadline - self._clock())

    @property
    def time_exhausted(self) -> bool:
        return self.remaining() <= 0.0

    def charge(self, kind: str) -> None:
        if self.time_exhausted:
            raise BudgetExhausted("deadline")
        if kind == "page":
            if self.page_requests >= self.max_page_requests:
                raise BudgetExhausted("page_requests")
            self.page_requests += 1
        else:
            if self.aux_requests >= self.max_aux_requests:
                raise BudgetExhausted("aux_requests")
            self.aux_requests += 1


def build_policy(*, total_timeout: float = 30.0, max_body_bytes: int = HTML_MAX_BYTES) -> NetPolicy:
    return NetPolicy(
        max_redirects=PUBLIC_MAX_REDIRECTS,
        connect_timeout=PUBLIC_CONNECT_TIMEOUT_S,
        read_timeout=PUBLIC_READ_TIMEOUT_S,
        total_timeout=total_timeout,
        max_body_bytes=max_body_bytes,
        user_agent=settings.user_agent,
    )


@dataclass
class AuditFetcher:
    client: SafeHttpClient
    budget: Budget
    base_policy: NetPolicy

    @classmethod
    def create(cls, budget: Optional[Budget] = None, client: Optional[SafeHttpClient] = None) -> "AuditFetcher":
        policy = build_policy()
        return cls(client=client or SafeHttpClient(policy), budget=budget or Budget(), base_policy=policy)

    async def fetch(
        self,
        url: str,
        *,
        method: str = "GET",
        kind: str = "page",
        max_body_bytes: Optional[int] = None,
        headers: Optional[dict[str, str]] = None,
    ) -> FetchResult:
        self.budget.charge(kind)
        remaining = self.budget.remaining()
        policy = replace(
            self.base_policy,
            total_timeout=min(self.base_policy.total_timeout, remaining),
            read_timeout=min(self.base_policy.read_timeout, remaining),
            connect_timeout=min(self.base_policy.connect_timeout, remaining),
            max_body_bytes=max_body_bytes or self.base_policy.max_body_bytes,
        )
        return await self.client.fetch(url, method=method, headers=headers, policy=policy)

    async def precheck(self, url: str) -> None:
        """Проверяет адрес политикой без запроса: схема, порт, userinfo, разрешение имени в публичные адреса.

        Поднимает NetPolicyError. Соединение всё равно проверяется заново при подключении.
        """
        target = parse_target(url, self.base_policy)
        await resolve_public(target.host, target.port, self.base_policy, self.client.resolver)
