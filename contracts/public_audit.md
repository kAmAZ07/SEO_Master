# Контракт: публичный аудит (REQ-PUBLIC, AC-01/AC-02)

Версия контракта: 1. Реализация: `services/audit_service` (`public_policy.py`, `netio.py`, `public_view.py`, `main.py`), `services/api_gateway/routes/public_routes.py`. Тесты: `tests/unit/test_public_audit_{core,lifecycle,gateway}.py`, фикстура `tests/fixtures/public_site.py` (версия 1).

Лимиты — стартовые значения RC, не измеренная ёмкость. Менять их можно только вместе с тестами и этим файлом.

## Жёсткие лимиты (применяются в audit-service на любом входе)

| Параметр | Значение |
|---|---|
| HTML-страниц | 10, глубина 2 (root = 0); клиентские `options` могут только уменьшить |
| JS-рендеринг, `respect_robots=false` | запрещены в публичном режиме |
| Дедлайн задания | 120 с (бюджет 112 с + запас на сборку результата) |
| Таймауты | connect 3 с, read до 10 с (в пределах остатка бюджета) |
| Редиректы | до 5 на запрос |
| HTML / robots.txt | 2 MiB / 512 KiB после распаковки |
| sitemap | 5 MiB после распаковки, 50 000 записей, 3 уровня индекса, до 20 файлов |
| Проверки ссылок | до 100 на задание; HEAD, при 405/501 — один GET |
| Параллельные задания | 3 на процесс audit-service, остальные ждут в `queued` |
| Хранение | 7 дней |

Основные запросы (HTML) и вспомогательные (robots, sitemap, HEAD) считаются раздельно, вместе ограничены бюджетом запросов и времени. Исчерпание бюджета даёт `partial`, а не ошибку.

## Жизненный цикл

`queued → running → completed | partial | failed | cancelled`; `expired` — производное состояние (см. ниже).

- `partial`: результат есть, но покрытие ограничено; причины в `summary.partial_reasons` (`deadline`, `page_requests`, `aux_requests`, `robots_blocked`, `robots_unreachable`, `sitemap_truncated`).
- `failed`: `summary.error_code` — `invalid_url`, `unsafe_target`, `dns_failure`, `deadline_exceeded`, `interrupted`, `internal_error`. Текст исключений наружу не отдаётся.
- Этап (`stage`) и счётчики (`pages_done`, `pages_max`) отражают реальную работу: `starting, precheck, robots, sitemap, crawl, analyze, links, psi, done`.
- Задание без обновлений дольше 150 с после перезапуска получает `failed/interrupted` при старте audit-service.

## HTTP

Шлюз:
- `POST /api/public/quick-audit` `{url}` → `200 {uid, status:"queued", status_url, expires_in_days, rate_limit}`; `422` невалидный/внутренний адрес (лимит не списывается); `429` + `Retry-After` — шестой принятый запуск в окне; `503` + `Retry-After` — Redis недоступен или audit-service недоступен (при отказе нашей стороны лимит возвращается).
- `GET /api/public/audit-status/{uid}` → `200` со статусом, `stage`, `progress` (0–100 по этапу и счётчикам), `counters`, `results` (после завершения), `expires_at`; `404` — неизвестный/некорректный uid или удалён; `410 {"detail": {"error": "audit_expired"}}` — срок хранения истёк.
- `POST /api/public/audit/{uid}/cancel` → итоговое состояние (`cancelled`, либо уже достигнутое конечное).
- `GET /api/public/rate-limit-info`.

audit-service (внутренний): `GET /public/audit/{id}`, `POST /public/audit/{id}/cancel`, `POST /audit/public`. Полные аудиты через `/public/*` недоступны.

## Ограничение частоты

5 принятых запусков на клиента за фиксированное окно 3600 с от первого принятого запуска. Клиент определяется по адресу соединения. `X-Forwarded-For`/`X-Real-IP` учитываются, только если соединение пришло от доверенного прокси (`PUBLIC_TRUSTED_PROXIES`, по умолчанию loopback и частные сети); цепочка разбирается справа налево, берётся первый недоверенный адрес. IPv6 считается по префиксу /64. Развёртывание за прокси, которого нет в списке, объединит всех клиентов в один счётчик.

## Отчёт

`results`: `summary` (покрытие, `checks`, оценка, `partial_reasons`, `psi`, `robots`, `sitemap`), `top_findings`, `findings_count`, `pages`.

- `checks.<имя>.status`: `passed | failed | unavailable | skipped`. Имена: `precheck, robots, sitemap, meta, h1, canonical, structured_data, links, psi`.
- PSI/CWV: источник указан (`summary.psi.source`), недоступность (`quota_exceeded`, `unavailable`, `deadline`) — отдельное состояние; критерий «Core Web Vitals» тогда не участвует в оценке и не вытесняет проблемы из top-5. LCP, INP, CLS — текущие метрики; FID хранится отдельно и оценивается своими порогами.
- `top_findings`: до пяти разных проблем (по `code`), порядок — severity, confidence, затем `(code, объект)`; `affected_count` — число затронутых объектов. Информационные записи не попадают. Если проблем меньше пяти — возвращается столько, сколько есть.
- Каждая запись: `code, title, description, recommendation, category, severity, confidence, affected_count, evidence` (только `url`, `status`, `canonical`, `count`, `len`, `value`, `reason`, `source`, `host`).
- Наружу не отдаются: HTML страниц, списки ссылок, тексты внутренних ошибок, поля, не входящие в белые списки `public_view.py`.
- Публичный результат — web-only, без CSV, без FF-Score, GSC/GA4 и HITL.

## Не входит в это изменение

- Токен переноса публичной сессии в личный проект (claim token) — AL-4.
- Перенос заданий в долговечную очередь (lease/checkpoints) — REQ-RELIABILITY.
- Подзапросы браузера и пул Playwright для полного аудита — отдельное задание.
