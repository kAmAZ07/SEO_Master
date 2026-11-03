"""Жёсткие лимиты публичного аудита (ТЗ REQ-PUBLIC §6.2, AC-01/AC-02).

Значения — стартовые для RC, не измеренная ёмкость. Менять их можно только вместе
с тестами и `contracts/public_audit.md`. Лимиты применяются на стороне audit-service
ко всем входам, поэтому прямой внутренний вызов не обходит ограничения шлюза.
"""
from __future__ import annotations

from datetime import timedelta

PUBLIC_MAX_PAGES = 10
PUBLIC_MAX_DEPTH = 2  # root = 0
PUBLIC_JOB_DEADLINE_S = 120.0
# Запас, который остаётся на сборку результата после того, как бюджет времени исчерпан.
PUBLIC_FINALIZE_MARGIN_S = 8.0
PUBLIC_CONNECT_TIMEOUT_S = 3.0
PUBLIC_READ_TIMEOUT_S = 10.0
PUBLIC_MAX_REDIRECTS = 5
PUBLIC_MAX_LINK_PROBES = 100
PUBLIC_MAX_SITEMAP_FETCHES = 20  # сам sitemap и дочерние файлы индекса

HTML_MAX_BYTES = 2 * 1024 * 1024
ROBOTS_MAX_BYTES = 512 * 1024
SITEMAP_MAX_BYTES = 5 * 1024 * 1024  # после распаковки
SITEMAP_MAX_ENTRIES = 50_000
SITEMAP_MAX_DEPTH = 3  # уровней вложенных sitemap index

PUBLIC_RETENTION = timedelta(days=7)
PUBLIC_MAX_CONCURRENT_JOBS = 3

# Основные запросы (HTML) и вспомогательные (robots, sitemap, HEAD, PSI) считаются
# раздельно, но вместе ограничены общим бюджетом запросов.
PUBLIC_MAX_AUX_REQUESTS = 1 + PUBLIC_MAX_SITEMAP_FETCHES + PUBLIC_MAX_LINK_PROBES * 2 + 2

PUBLIC_STATUSES = ("queued", "running", "completed", "partial", "failed", "cancelled", "expired")
FINAL_STATUSES = frozenset({"completed", "partial", "failed", "cancelled", "expired"})


def public_options(requested: dict | None = None) -> dict:
    """Опции публичного запуска. Значения пользователя могут только уменьшить лимиты."""
    requested = requested or {}

    def _clamped(name: str, ceiling: int, floor: int) -> int:
        try:
            value = int(requested.get(name, ceiling))
        except (TypeError, ValueError):
            return ceiling
        return max(floor, min(ceiling, value))

    return {
        "max_pages": _clamped("max_pages", PUBLIC_MAX_PAGES, 1),
        "max_depth": _clamped("max_depth", PUBLIC_MAX_DEPTH, 0),
        "js_render": False,
        "respect_robots": True,
        "concurrency": 1,
        "timeout": PUBLIC_READ_TIMEOUT_S,
    }
