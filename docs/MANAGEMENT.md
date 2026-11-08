# Management: evidence, версии и решения HITL

## Объём инкремента

Стык Audit → AL-5 → Management, REQ-EVIDENCE §7, REQ-SEMANTIC §9.1/9.4 и REQ-HITL §10; AR-4 по декомпозиции. Реализованы приём баллов, подготовка конкретной версии к согласованию и строгий путь пользовательского решения. Полный AR-4, AR-3 и AC-07/08/09 ещё не завершены.

Основа исследования — `ff6d75b` (PR #104, AL-5). В этой базе уже есть работа Александры: auth/session и сохранение error headers, SSRF client, публичный аудит, workspace/ownership AL-4, затем AL-5 — `RecommendationVersion`, nullable FF-Score/E-E-A-T и ограниченная последовательная политика LLM. Наличие исходников и merge не принимается за доказательство всех AC; полный кодовый контракт AL-5 описан в `docs/SEMANTIC.md`.

## HTTP-результат расчёта

Management использует исходную `services.semantic_service.schemas.ff_score.FFScoreResponse`, не копию схемы и не runtime-приложение Semantic. Модуль `services.management_service.score_contract` проверяет нужные потребителю свойства ответа:

- `ff_score_v1` и `eeat_v1`, идентификаторы результата и согласованность E-E-A-T score ID;
- полный балл — конечное число 0…100 при `complete` либо явный `null` при `insufficient_data`;
- bool, числовые строки, NaN/Infinity, неизвестные статусы/формулы и противоречия отвергаются как `semantic_score_contract_invalid`, без раскрытия тела ответа;
- отсутствие ответа и неверная схема не принимаются за успешно завершённый расчёт.

При `insufficient_data` сага достигает `scores_completed`: завершился расчёт, но полного балла нет. В `saga_context.score_result` остаётся ответ с components/inputs/coverage/partial; отдельные status/formula fields сопровождают полный балл. Частичный результат не заменяет полный и не получает его название.

Автоматический retry вокруг всей `_trigger_scores_calculation` удалён: POST сохраняет результаты и публикует событие, но не имеет контракта idempotency key. При ошибке/потере ответа нет слепого повторения всей последовательности; ошибка идёт по существующему пути отказа саги. Полноценное восстановление этой стадии требует отдельного механизма.

## События

Потребитель проверяет используемые поля `ffscore_recalculated/2` до первого запроса к БД. `ff_score`, aliases E-E-A-T, статусы и версии формул должны согласовываться. Дополнительные поля события остаются ответственностью исходного протокола AL-5; это проверка проекции Management, а не новая полная схема envelope.

Полученный `null` записывается и в проект, и в task metadata: старое число не остаётся текущим. Измеренный 0 сохраняется. Для JSONB создаётся новый dict вместо изменения прежнего объекта на месте. Сохраняются status, formula version и время обработки.

Для payload без `schema_version` сохранена ограниченная legacy-совместимость с числовыми `ffscore`/`eeat`; formula маркируется `legacy`, не `ff_score_v1`. Отсутствующее legacy-поле E-E-A-T не стирает прежнее значение, но явный `null` стирает. Противоречащий alias не заменяет canonical null. Неизвестный явно заданный schema version отвергается.

## Evidence и подготовка предложения

Пользователь явно разрешил включить необходимый стык Audit → Semantic в текущую разработку. Полный аудит сохраняет `pages[].page_evidence` в существующем JSONB вместе с конечным результатом. Снимок содержит project/audit ID, исходный и конечный URL, HTTP status, SHA-256 сохранённого HTML, факты страницы и число JSON-LD блоков. `recorded_at` — время записи результата, не заявленный момент HTTP fetch. Основной текст ограничен 50 000 символами; `text_truncated` отмечает сокращение. Отсутствие title/description отличается от пустой строки, сохраняются все H1 и точный текст единственного JSON-LD блока. Из scripts/styles не извлекается основной текст. Публичное представление отбрасывает весь новый envelope.

`shared.page_evidence` задаёт SHA-256 канонического снимка с доменом `SEO-MASTER-PAGE-EVIDENCE-V1` и content-addressed UUID5 evidence ID. Это отдельный хеш, не recommendation hash и не подпись сервиса. Повторное чтение сохранённого снимка сохраняет ID/hash. Изменённые факты или метаданные дают другой hash/ID. `_update_row` блокирует строку через `FOR UPDATE` и запрещает изменение terminal audit: конкурирующая/поздняя финализация не заменяет снимок. Защита действует в этом write path, не является PostgreSQL trigger против произвольного SQL. Новая схема/миграция не требуется; исторические аудиты без envelope требуют нового аудита.

После завершённого full-аудита Management выбирает ровно одну страницу с точным каноническим target URL. Проверяются целостность снимка, совпадение project/audit/URL и отсутствие redirect на другую страницу. Подмена первой страницей списка запрещена. Факты проходят существующую `PageFacts` производителя AL-5. Неполный аудит, отсутствующая страница/evidence, несколько JSON-LD блоков для изменения JSON-LD дают `needs_input`. Ошибка scope/подмена переданного evidence — отказ.

`POST /internal/optimization/run` принимает необязательный `proposal_operation` (`title`, `description`, `h1`, `schema_jsonld`) без присланных фактов: факты/evidence берутся из нового аудита. Без явной операции выбираются находки только целевой страницы; при нескольких операциях нельзя молча взять первую, результат — `needs_input`. Старый необязательный `proposal_request` проверяется на точное совпадение фактов и evidence текущего аудита; произвольный payload не заменяет источник.

Management вызывает `/semantic/proposals`, проверяет существующую `RecommendationVersion` исходным валидатором AL-5, включая hash, before, scope, evidence, operation, derived checks и execution mode. `needs_input`/`no_change` не превращаются в изменения. HITL preview сохраняет точную версию и состояния before/after. Результат подготовки — `awaiting_hitl`, `publication_status=not_requested`: сага не ждёт решения и не вызывает CMS или событие успешной публикации. Старая автоматическая apply/false verification ветка закрыта; defaults approve/batch `auto_deploy=false`, явный auto-deploy даёт 409. Старые HTTP-решения с одним `user_id` больше не принимаются; строгий путь описан ниже.

Образы Management копируют только шесть необходимых чистых исходников Semantic, без runtime-приложения/настроек Semantic. Собранный Linux-образ проверен на импорт этих контрактов без БД, под UID 10001, без сети и записи в rootfs. Это не проверка старта всего приложения.

## Строгое пользовательское решение AR-4

`approval_identity` требует одновременно `X-Internal-API-Key` и исходный access token пользователя. Токен пересылается только в настроенный `API_GATEWAY_URL/api/auth/me`: существующий auth-service проверяет подпись, type/expiry/issuer/audience, сессию и активность пользователя. HTTP redirect запрещён, proxy environment не используется, timeout — 5 секунд. При недоступной authority — 503, при недействительной сессии — 401. `user_id`/роль из тела не принимаются. Секрет или токен не сохраняются в approval. API Gateway проксирует тот же token и внутренний ключ; отдельного нового JWT-валидатора в Management нет.

`approval_service` читает канонические `audit_schema.projects`, `workspace_members` и `public.users`: требуется активный пользователь с действующим членством и правом `APPROVE` (owner/admin/reviewer). Viewer/deployer могут читать, но решение даёт 403; чужой и отсутствующий объект дают 404. Один домен в двух workspace не объединяет права. Перед решением повторно проверяются scope проекта, URL задачи, неизменённый preview, последняя сохранённая версия Semantic и последний завершённый full-аудит именно целевой страницы. Любой новый снимок этой страницы заменяет evidence reference и требует нового решения, даже если часть текста совпадает.

Маршруты `/api/v1/hitl/tasks/{task_id}/approve`, `/reject`, `/revoke` принимают `approval_id`, `recommendation_version_id`, `content_hash` и необязательный `comment` (до 1000 символов). Browser/API Gateway отправляют ссылки из показанного preview, не подбирают текущую версию на сервере. Несовпадение или повторное решение — 409. `auto_deploy=true` не разрешён. HITL-часть Dashboard тоже отправляет эти ссылки; устаревшие карточки не разрешают решение. Это необходимая адаптация потребителя, не завершение всего AR-8.

Task и approval читаются через `populate_existing` и `FOR UPDATE` в одном порядке; membership/project/user удерживаются `FOR SHARE` до commit. Решение и статус задачи сохраняются одной транзакцией. Конкурентные approve/reject получают один результат и один 409; прежний ORM cache не позволяет перезаписать решение. Результат успешного approve: `publication_status=not_requested`, `auto_deployed=false`.

В существующем `HITLApproval.metadata` сохраняются `decision_record` (`hitl_decision_v1`), `decision_hash` и комментарий отдельно. Неизменяемая запись включает approval/task/project/workspace, version/hash, target URL, actor/фактическую роль, decision, decided_at/expires_at. Хеш SHA-256 имеет отдельный домен `SEO-MASTER-HITL-DECISION-V1`; это digest записи в авторитетной БД, не HMAC и не автономный CMS authorization envelope. Комментарий не меняет payload/hash. Новых колонок и DDL в этом инкременте нет.

Базовый срок — 24 часа; `HITL_APPROVAL_TTL_HOURS` допускает 1…168. Ровно в `expires_at` запись уже expired. Отзыв сохраняет первоначальный decision/hash и добавляет revocation с actor/ролью/временем; повторное одобрение той же записи запрещено. Revoke не меняет решение при `IN_PROGRESS`/`DEPLOYED`. На чтении `authorization_status` вычисляется заново: pending/approved/rejected/revoked/expired/invalidated. Новая версия, изменённый target/preview/evidence или потеря роли у первоначального approver дают invalidated. Legacy `status=APPROVED` сам по себе не доказывает действительность разрешения.

Оба старых Management-входа `ClientAPIAdapter.deploy_changes` и `deploy_task_changes` теперь отказывают **до транспорта и записи статуса** с `version_bound_executor_required`: произвольный `task.meta` не может воспользоваться новым `APPROVED`. API Gateway больше не публикует собственное `HITLApproved` после проксирования. Надёжный outbox и единственный Client Gateway executor ещё должны быть реализованы; факт решения не выдаётся за доставленное событие или публикацию.

## Проверки и следующие зависимости

`tests/unit/test_management_scores.py` обязателен в management-профиле: HTTP через `httpx.MockTransport`, настоящая общая Pydantic-схема, переход состояния саги, нулевые/отсутствующие значения и отказ до DB query/write. DB fixtures проверяют обработчик и замену JSON-объекта, не настоящий PostgreSQL transport/commit. Запуск: `python scripts/backend.py check management` в предусмотренном Python 3.11 tooling-окружении.

Проверено в рабочем snapshot поверх `ff6d75b`, Windows x86_64 / Python 3.11.9. Раздельные окружения устанавливались из существующих test-locks с проверкой хешей; рабочий `.venv` не синхронизировался:

- Две целевые регрессии на исходных `orchestrator.py` и event handler из HEAD: обе RED по нужным причинам — отказ при допустимом null и сохранение старого балла 85. Исходники заменялись только во временной тестовой копии.
- Итоговый management-профиль: 131 passed, 1 deprecation warning; включает RED → GREEN для недоверенного `user_id`, публикации произвольного `task.meta` и независимой копии JSONB saga context. Native ORM/import probe использует SQLAlchemy 2.0.55 и Pydantic 2.5.3.
- API-профиль: 514 passed, 81 warning. Новые HTTP-тесты проверяют точную передачу version/hash и access token, viewer/deployer, пользователя другого workspace с тем же доменом и запрет body identity.
- Итоговый audit-профиль: 80 passed, 537 deprecation warnings существующих зависимостей/`on_event`; предупреждения не подавлялись.
- Неизменённый semantic-профиль Александры: 534 passed, 6 warnings на собственном lock с Pydantic 2.10.5. Предупреждения не скрывались.
- Platform regressions: 95 passed; strict mypy для действующей платформенной области — 16 файлов; отдельно для score/evidence contracts и трёх новых approval-модулей с `--explicit-package-bases --follow-imports=silent`. Legacy ORM описан узкими typed instance views без изменения metadata. Ruff изменённых Python-файлов — без ошибок.
- Frontend: targeted Vitest — 5 passed; ESLint и production build прошли. В предупреждениях остаются существующие Vite/React tooling и старые Browserslist data; зависимости этим изменением не обновлялись.

Это результаты локального source/unit-прогона, не новый GitHub Actions run и не PostgreSQL/WordPress/staging-приёмка. Функциональные изменения и нормализация EOL затрагивают только перечисленные в diff файлы; весь checkout не перенормализовывался.

Отдельно `tests/integration/test_audit_evidence_postgres.py` проверяет настоящие JSONB commit/read и конкурентную финализацию на одноразовом PostgreSQL 16: 1 passed. Использованы audit-test lock и закреплённые Alembic/Mako/MarkupSafe из management-test lock для существующей интеграционной фикстуры. Создаются только собственные временные БД; после проверки контейнер удаляется. Таблица/partitions создаются как тестовая ORM fixture: это доказательство поведения write path и передачи фактов, **не** приёмка migrations/startup DDL, approvals или WordPress. Запуск требует `TEST_PG_ADMIN_URL` и integration-окружения с этими пакетами; сетевой unit-плагин для этого отдельного integration suite не применяется.

`tests/integration/test_management_approvals_postgres.py`: 21 passed на PostgreSQL 16 и management-test lock. Настоящие транзакции проверяют роли/scope, version/hash, границу TTL, revoke, изменённый preview/target/evidence, новую версию, stale identity map и конкурентные approve/reject. Отдельный сценарий инжектирует исключение **после настоящего commit** и проверяет reconciliation/отказ повторного решения; это не сетевой или process-crash test. Native fixture создаёт ORM-таблицы и явно равные canonical/legacy project UUID; она не доказывает миграцию старых проектов, runtime grants или готовность рабочего стека.

Открытые задачи:

- Durable crawl/checkpoint, полные сетевые provenance и budgets AL-8 этим узким стыком не завершаются. Снимок связывает факты с сохранённым аудитом, но не доказывает их свежесть к будущему apply; перед исполнением нужны preconditions/revalidation.
- Весь AR-4/AC-07 ещё не принят. Остаются история решений и безопасная подготовка нового approval при регенерации (legacy `task_id` уникален в `hitl_approvals`), транзакционный outbox/inbox для решения, единый Client Gateway executor с авторитетной проверкой перед первой CMS-мутацией и AR-6 DeploymentOperation. Закрытые старые adapter-входы не являются реализацией нового executor.
- FK `Task/HITLApproval → public.projects` пока legacy; canonical projects живут в `audit_schema.projects`. Новый путь не делает host-based mapping и не создаёт shadow projects. Выравнивание FK/данных и runtime grants требует AR-3; на произвольной рабочей БД этот инкремент не объявляется готовым к сквозному запуску.
- Приоритизация и формула unknown impact — AR-10, этот инкремент их не меняет.
- Out-of-order/replay, атомарность inbox/business commit и freshness по времени источника не решены проверкой чисел; нужны последующие события/EDA задачи.
- AR-3 schema/role/startup DDL и gates `up/e2e` остаются открыты. Этот инкремент не подключается к рабочей БД и не применяет миграции.
