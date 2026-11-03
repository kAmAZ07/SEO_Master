# Контракт: проект, workspace и подтверждение прав на сайт (REQ-CORE §3.2–3.3, REQ-SECURITY §5.2, AC-03)

Версия контракта: 1. Реализация: `shared/projects/` (чистое ядро), `services/api_gateway/projects/` (сервисный слой), `services/api_gateway/routes/protected/projects_routes.py`, миграция `database/migrations/versions/20261103_001_workspaces.py`. Тесты: `tests/unit/test_project_*.py`, `test_domain_verification*.py`, `test_projects_gateway.py`, `test_public_audit_claim_token.py`, `tests/integration/test_migrations_postgres.py`.

## Идентификатор проекта

**`project_id` — строка UUID (v4, 36 символов, нижний регистр, с дефисами)**, создаётся шлюзом при создании проекта и не меняется. Он передаётся строкой в API, событиях и колонках других сервисов (`String(36)`). Каноническая сущность — `audit_schema.projects`; других источников истины о проекте быть не должно. Сервисы не генерируют собственные идентификаторы проектов и не принимают `user_id`/`owner_id`/`workspace_id` из тела запроса как источник идентичности.

Колонки `project_id` в таблицах результатов (`crawl_results`, `public_audit_results`, `ff_scores`, …) остаются строками без внешнего ключа, как и раньше. Проект management_service (`public.projects`, UUID, домен) — не канонический; соответствие хранится в `audit_schema.legacy_project_id_map` (см. ниже).

## Область проекта

Область = `origin` (схема, домен в ASCII/punycode, порт) + `path_prefix`. Нормализация (`shared/projects/scope.py`):

- домен приводится к нижнему регистру и punycode; `www.example.com` и `example.com` — разные области;
- порты по умолчанию (80/443) не пишутся; допускаются только 80 и 443 (политика `shared/netpolicy.py`);
- `path_prefix`: `/` или `/a/b` без завершающего слеша; `.`/`..` разрешаются, `%XX` декодируются и кодируются заново в каноническом виде; регистр пути значим;
- query и fragment отбрасываются;
- неоднозначные записи (закодированные `/` и `\`, управляющие символы, пустые сегменты, `..` выше корня, userinfo, нестандартные порты, зарезервированные имена и непубличные IP-литералы) отклоняются кодом `422 scope_invalid`;
- принадлежность адреса области: те же схема/домен/порт и префикс **по границе сегмента** (`/blog` содержит `/blog`, `/blog/x`, но не `/blogger`); любая неоднозначность — «вне области».

Уникальность — `(workspace_id, origin, path_prefix)`. **Домен не блокируется глобально**: два пользователя могут создать проекты на один и тот же сайт, каждый видит только свой. Повторное создание той же области тем же пользователем идемпотентно (`200` с существующим проектом).

## Workspace и роли

У каждого пользователя одно личное workspace (`kind='personal'`), создаётся при первом создании проекта; уникальность обеспечивает частичный индекс БД. Доступ к любому объекту проекта проверяется через членство (`workspace_members`): чужой и несуществующий проект неразличимы (`404`), недостаточная роль участника — `403 insufficient_role`.

| Роль | read | manage | approve | deploy |
|---|---|---|---|---|
| owner, admin | да | да | да | да |
| reviewer | да | — | да | — |
| deployer | да | — | — | да |
| viewer | да | — | — | — |

В RC используется только `owner` личного workspace; приглашения участников не реализованы, но схема и проверки уже работают по ролям. Хелпер для остальных сервисов шлюза: `services/api_gateway/projects/access.py::resolve_project(db, user_id, project_id, permission)`.

## HTTP (шлюз, требуется авторизация)

| Метод и путь | Права | Ответ |
|---|---|---|
| `GET /api/projects` | read | проекты всех workspace пользователя |
| `POST /api/projects` `{name, url, description?, sourceAuditId?, claimToken?}` | — | `201` новый, `200` существующий; `422 scope_invalid` |
| `GET /api/projects/{id}` | read | проект + `scope` + `verification` |
| `DELETE /api/projects/{id}` | manage | `{success}` |
| `GET /api/projects/{id}/verification` | read | сводка (см. ниже) |
| `POST /api/projects/{id}/verification/challenge` | manage | `201 {fileUrl, fileBody, expiresAt, verification}`; токен показывается один раз |
| `POST /api/projects/{id}/verification/check` | manage | `{verified, errorCode, verification}`; `409 no_pending_challenge`; `429 check_too_frequent` + `Retry-After` |
| `DELETE /api/projects/{id}/verification` | manage | отзыв; `409 nothing_to_revoke` |

Сводка `verification`: `state` (`unverified | pending | verified | stale`), `scope`, `freshDays`, `verifiedAt`, `freshUntil`, `challengeExpiresAt`, `attempts`, `lastErrorCode`, `lastStatus`.

## Подтверждение: HTTP-challenge

1. `POST …/verification/challenge` возвращает адрес `fileUrl` (`<origin><path_prefix>/seo-master-challenge.txt`) и содержимое `fileBody` (`seo-master-site-verification=<токен>`). Для области `/blog` файл кладётся в `/blog/`, для всего хоста — в корень. Подкаталог не подтверждает хост.
2. `POST …/verification/check` запрашивает файл через `SafeHttpClient` (SSRF-политика, **без редиректов**, тело до 4 КиБ, таймаут 10 с). Принимается только ответ `200`, не `text/html`, целиком равный строке challenge (BOM и перевод строки допустимы).
3. В базе хранится только `SHA-256(v1 | workspace_id | origin | path_prefix | token)`: токен, выданный для другой области или workspace, не подходит. Сравнение — `hmac.compare_digest`.

Состояния `domain_authorizations.status`: `pending → verified | expired | failed | revoked | superseded`, `verified → superseded | revoked`; остальные конечные (таблица `shared/projects/authorization_state.py`). В БД не больше одной записи `pending` и одной `verified` на проект (частичный уникальный индекс). Новый challenge отменяет прежний невыполненный (`superseded`); успешная повторная проверка заменяет прежнее подтверждение атомарно.

| Параметр (настройка шлюза) | По умолчанию |
|---|---|
| `DOMAIN_CHALLENGE_TTL_HOURS` — срок challenge | 24 |
| `DOMAIN_VERIFICATION_FRESH_DAYS` — срок актуальности подтверждения (виден в API как `freshDays`) | 30 |
| `DOMAIN_CHECK_MIN_INTERVAL_SECONDS` — интервал между проверками | 5 |
| `DOMAIN_CHECK_MAX_ATTEMPTS` — попыток на challenge, затем `failed` | 20 |
| `DOMAIN_VERIFICATION_ENFORCED` — требовать свежее подтверждение для полного обхода | `true` |
| выдача challenge | не более 20 в сутки на проект |

Коды `errorCode` проверки: `http_<status>`, `redirected`, `bad_content_type`, `malformed_body`, `token_mismatch`, `fetch_<код netpolicy>` (`fetch_address_blocked`, `fetch_dns_failure`, `fetch_timeout`, `fetch_tls_error`, …), `challenge_expired`, `attempts_exhausted`, `verification_conflict` (повторить), `challenge_no_longer_pending`.

## Что требует подтверждения

До подтверждения доступны: создание и хранение проекта, ограниченный публичный аудит. Требуют **свежего** подтверждения (ТЗ §5.2): полный обход (`POST /api/audit/full`), расписание и публикация в CMS. Для зависимых операций:

```python
service = DomainVerificationService(db, make_fetch(), VerificationConfig.from_settings(settings))
service.require_fresh(project)             # DomainNotVerified (403): not_verified | stale | scope_changed
service.require_fresh(project, url)        # + UrlOutOfScope (422), если url вне области
```

Вне зависимости от подтверждения `POST /api/audit/full` отклоняет адрес вне области проекта (`422 url_out_of_scope`) и проект без области (`422 project_scope_unresolved`). Смена домена/области проекта делает прежнее подтверждение недействительным (`scope_changed`). Для локальной разработки проверку свежести можно выключить `DOMAIN_VERIFICATION_ENFORCED=false`; проверка области остаётся.

## Присоединение публичного аудита к проекту

Идентификатор публичного аудита доступен по ссылке всем, поэтому он не доказывает авторство. `POST /api/public/quick-audit` возвращает дополнительно `claim_token` (одноразовый, виден только в этом ответе; в базе — `public_audit_results.claim_token_hash`). Проект создаётся с `sourceAuditId` + `claimToken`; присоединение выполняется, если токен верен, аудит публичный, не истёк, ещё не присоединён и его `root_url` внутри области проекта. Токен после этого сгорает. Ответ создания проекта содержит `claim: {status: "claimed" | "rejected", reason}` (`invalid_claim`, `already_claimed`, `out_of_scope`); проект при отказе всё равно создаётся. Причины «аудита нет» и «токен неверен» не различаются. Клиент хранит токен в `localStorage` (`frontend/src/api/auditClaim.ts`).

## Соответствие идентификаторов других сервисов

`audit_schema.legacy_project_id_map(source, legacy_id, project_id, status, reason)`; `source='management.projects'`. Миграция сопоставляет проект management только если домен совпал **ровно с одним** каноническим проектом (`matched`); иначе `unmatched` (нет такого проекта) или `ambiguous` (домен у нескольких пользователей) — такие записи разбираются вручную, автоматически проекты разных пользователей не объединяются. Таблицы management миграция не меняет: перевод management_service на канонический `project_id` — отдельная задача.

## Миграция и откат

`20261103_001_workspaces`: создаёт workspace и членство, переносит каждый проект в личное workspace владельца, считает область, снимает глобальную уникальность URL, добавляет `domain_authorizations`, `legacy_project_id_map`, `claim_token_hash`. Данные не удаляются: проект с неразбираемым URL или с областью, уже занятой более ранним проектом того же владельца, остаётся с пустой областью и статусом `scope_review` (читается, но не допускается к зависящим от области операциям). Откат возвращает схему; глобальная уникальность URL восстанавливается, только если дубликатов нет (иначе откат отказывает с понятной ошибкой до любых изменений). Свежая установка через `database/init_schema.sql` даёт ту же схему (проверяется тестом).
