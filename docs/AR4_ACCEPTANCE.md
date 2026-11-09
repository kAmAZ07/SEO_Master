# AR-4: реализация и доказательство приёмки

Дата: 09.11.2026. База завершающего инкремента: `d4810dd0275febb3ed4cfd8c336234ed8e95495c`, ветка `arseniy/ar-4-score-handoff`. Проверяется рабочая ревизия этого инкремента; итоговый commit определяется Git. Требование: REQ-HITL §10, primary AC-07; стыки с §3.2/5.2/7/11/12. AL-5 используется из реального `RecommendationVersion`, отдельной копии схемы нет.

## Текущее состояние

Реализован и проверен native путь: сохранённая версия/evidence → явное человеческое решение → отдельная команда → Management authority → единственный Gateway executor → реальный WordPress → подписанный receipt → `applied_unverified`. Неподготовленная/неодобренная версия не достигает CMS. Одобрение само CMS не меняет.

**AR-4 по REQ-HITL §10 / AC-07 закрыт в проверенном объёме.** Предыдущий startup-блокер устранён: Client/Audit/Semantic и event tracking больше не создают схему при запуске. Client storage получает явную migration revision, Core — enforced producer barrier. Native HTTP stand запускает настоящие API, Management и Client lifespans с тремя отдельными ролями PostgreSQL без DDL, Redis sessions и RabbitMQ; данные общей dev/рабочей БД не используются.

Общий `up/e2e` остаётся закрыт: полный AR-3/AL runtime grants, browser egress и readiness всего стека не принимаются этим узким сценарием. Это отдельная граница платформы, а результат AR-4 оценивается по AC-07 и работающему пути трёх приложений. Создание PR допускается после финального зелёного прогона; это не означает слияния или production deploy.

Verify/rollback и полная модель доставки AR-6/7, browser/staging сценарий AR-9, совместимость тем и выпуск плагина AR-15/17 остаются отдельными задачами. Receipt не означает `verified`.

## Авторитет и состояния

- Management владеет approvals и создаёт frozen DeploymentOperation через `ExecutionService.reserve`. Используются зарезервированные строки существующего `management_changelog`, без седьмой ORM-таблицы. `record` содержит ссылки, project/workspace, actor/role, decision hash, полную AL-5 версию, server binding, exact before/after и CMS command/request hash.
- Revision `20261109_003_execution` добавляет partial unique index на task/version и защиту frozen record/переходов/удаления. Текущий Management head — `20261109_004_client_storage`; Core head — `20261109_001_hitl_barrier`. Сначала применяется Core, затем явный Management runner; startup читает schema/index/trigger/role, не создаёт их.
- `ready → applying → applied_unverified | no_op | outcome_unknown`. Только неотправленный `ready` можно отменить. Истёкший ready отменяется при явном резервировании нового ID; отправленная или неизвестная операция никогда не возвращается в ready.
- `begin` — точка принятия исполнения: enforced advisory barrier Core, fresh Task/Approval/operation locks и проверки после ожиданий. Проверяются original actor/DEPLOY, активный canonical project, approver/APPROVE, версия/hash/target/evidence, решение, domain verification и время. Подготовленная версия и человеческое решение заморожены для in-flight операции; новое решение/late saga failure не заменяет её. Позднее изменение membership относится к будущим операциям.
- Domain challenge `expires_at` не является сроком владения: действительность verified ownership определяется `verified_at + DOMAIN_VERIFICATION_FRESH_DAYS`, как в Core. Срок CMS command ограничен минимумом approval/domain expiry и 60 секунд. Нужны доверенные синхронизированные часы; native fixture использует один хост.
- Client Gateway выполняет readonly preflight/dry-run, вызывает begin и отправляет ровно frozen command. При неизвестном результате повторяется только signed status/reconciliation; новые transport timestamp/nonce не меняют operation ID/body/hash.
- WordPress target-bound routes проверяют target URL из заблокированных post/options/parents, не из WP caches. Unknown rewrite/URL/Options filters дают unsupported. Поддержаны plain URLs и ограниченный набор postname/post_id, index.php, page hierarchy/static front page; taxonomy/author/custom rewrite остаются unsupported. Deadline проверяется свежими часами после CMS locks и непосредственно перед первым write.
- Поддержанные CMS-поля: title, description, JSON-LD; H1/content/interlinks/Tilda и отсутствие подтверждённого entity дают manual/unsupported, без успешной записи. Exact before сохраняет missing/value/пустую строку.
- HITLApproved создаётся из реального решения в immutable history/outbox. Consumer сверяет exact outbox envelope, проверяет history, вставляет Core `processed_events` и commit до ACK. Повтор не создаёт решение/apply. Runtime inbox DDL отсутствует; ограниченные broker retries идут в существующие 10/60/300s очереди, затем DLQ.

## API, интерфейс и конфигурация

`POST /api/hitl/tasks/{task_id}/apply` принимает только `operation_id`, `task_id`, `approval_id`, `recommendation_version_id`, `content_hash`. API требует DEPLOY, пересылает original Bearer JWT в Client Gateway, который перепроверяет его через Management. `GET /api/hitl/tasks/{task_id}/executions/{operation_id}` читает scoped статус. Arbitrary changes, body actor и несовпадение path/task отвергаются.

В HITL UI кнопки «Одобрить» и «Применить одобренные изменения» отдельные. Одобрённая карточка остаётся видимой. In-flight/unknown предлагает проверку результата той же операции, включая потерю ответа после замены expired ready. `applied_unverified` прямо сообщает об отсутствии независимой проверки. Это минимальный пользовательский стык AR-4, не весь экран AR-8.

Management internal execution endpoints требуют обычный service key и **отдельный** executor credential. Только Management web и Client получают его; worker/beat Compose его не получают. Bootstrap добавляет новый `MANAGEMENT_EXECUTOR_API_KEY` в старую проверенную private dev-конфигурацию без ротации существующих ключей. Прямой Management запуск использует `EXECUTOR_API_KEY`; Compose отображает один credential в два соответствующих settings.

Server-owned binding задаётся через `WORDPRESS_V2_TARGETS_JSON`, например:

```json
{"11111111-1111-4111-8111-111111111111":{"base_url":"https://your-site.example","site_id":"33333333-3333-4333-8333-333333333333","key_id":"current","secret_ref":"env:SEO_MASTER_HMAC_SECRET","receipt_secret_ref":"env:SEO_MASTER_RECEIPT_SECRET"}}
```

Оба независимых случайных CMS секрета — 64–128 lowercase hex, как у плагина. JSON содержит только env references. В штатном Compose эти два имени явно передаются Client; для нескольких targets отдельные имена передаются через контролируемый deployment override/secret provider. Caller не выбирает адрес или credentials. Без binding/ключей публикация отвечает 503.

Private dev-файл `.seo-local/dev/environment.env` допускает эти три optional поля и `DOMAIN_VERIFICATION_FRESH_DAYS`; рабочий корневой `.env` runner не читает. Общий срок freshness передаётся API и Management одинаково. Секреты не попадают в ответы/образы/отчёт.

## Трассировка проверок

| Требование → AC-07 | Проверка | Фактическое доказательство / граница |
| --- | --- | --- |
| Actor/session/role/object scope; body identity запрещена | unit auth/HITL; native approvals; `app_http.py` | Реальные HTTP register/me, JWT access/refresh separation и Redis sessions; чужой пользователь/понижение роли/forged internal request не достигают CMS. Browser E2E не заявляется |
| Immutable version/hash/target/evidence, TTL24h, revoke | `test_management_approvals_postgres.py`, `test_management_outbox_postgres.py` | Настоящие transactions, refresh старого identity map, approve/reject races, history/outbox atomicity, regeneration |
| Единственный executor, frozen payload, Task lifecycle | `test_hitl_handler.py`, `test_management_proposals.py`, `test_management_execution_postgres.py` | Legacy writers отказывают до DB/CMS, PATCH не задаёт system status/metadata; DB immutable operation/unique version; конкурентный begin — один победитель |
| Отказы до первой CMS-мутации | native execution suite + `tests.wordpress.native_contract` | PostgreSQL: expiry/revoke/role/domain/actor/before/hash. Настоящий WordPress: URL/CAS/deadline/filter отказы, истечение во время реального SQL lock wait; SEO/ledger не изменены |
| Replay/неизвестный результат | `test_client_gateway_rollback.py`, native execution и WordPress suites | Инжектированная потеря apply response → unknown → status only; настоящий WP: два параллельных same-op HTTP → одинаковый signed receipt и одна ledger-операция. Не process-kill test |
| Human record → outbox → inbox | native outbox и execution suite | Exact envelope/history, durable processed_events, commit до двух ACK, forged payload отвергнут, CMS/operation count=0. HTTP stand использует настоящий publisher и живой consumer с отдельной runtime-ролью; consumer-restart/crash не заявляется |
| Просмотр → одобрение → отдельное применение | frontend/API suites; native authority integration | UI/API references и отдельные действия; Фактические Core+Management migrations, AL-5 payload/evidence, approval, Gateway, mysqli и receipt связаны HTTP-сценарием; register/project создаются API. Audit facts/version/domain verification подготовлены как fixtures |
| Упаковка | Client и Management Docker build/import probes | Linux images с собственными runtime locks, UID10001, read-only FS и ограниченными tmpfs; реальные API/Management/Client lifespans, routes и consumer. No-DDL роли не владеют таблицами и не могут менять stamps |

Предыдущий проверенный инкремент: Management unit **149 passed**; Client unit **11 passed**; PostgreSQL migration/approval/outbox/execution **79 passed**; API **518 passed**; frontend **10 passed**, production build, TypeScript и ESLint; окончательный полный platform suite **96 passed**, включая bootstrap upgrade. Профили используют собственные locks: Python3.11.9, Management SQLAlchemy2.0.55/Pydantic2.5.3, Client/API SQLAlchemy2.0.25/Pydantic2.14.2; Node24.14.1.

PHP standalone: syntax17, legacy8/8, protocol41/41, adapter38/38, scripted store35/35 и shared Python/PHP vectors. Эти цифры не заменяют настоящий WordPress. MariaDB `bigint(20) unsigned` дефект сначала воспроизведён падающим scripted regression, затем исправлен точным признанием того же unsigned64 типа; signed тип по-прежнему отвергается.

Native command из окружения с закреплённым `locks/management-test.txt`:

```text
python -B -m tests.wordpress.native_contract --docker-context desktop-linux --with-management --with-apps
```

Linux/CI использует `--docker-context default`. Образы WordPress/MariaDB/PostgreSQL закреплены digest в `tests/wordpress/native.py`; создаются собственные containers/network/tmpfs и случайные credentials. Перед очисткой проверяется owner label. Application config импортируется из пустого cwd с очищенным окружением. Native WP требует strict SQL, явно настроенный только для fixture; guard production storage не ослаблен. Native job добавлен в CI; локальный pass не означает уже прошедший GitHub Actions run.

Независимый read-only reviewer проверил safety diff. Замечания о permalink/Options filters, legacy lifecycle writers, packaging, env injection и stable UI retries исправлены; автор отдельно проверил изменения и приведённые результаты. Набор «все 31 мутация RED» не заявляется.


## Миграционный стык и синхронизация текущего инкремента

Сохраняются две явные линии: Core/audit и Management/control-plane. После Core head применяется проверенный Management runner до `20261109_004_client_storage`. Эта frozen revision управляет только двумя Client-таблицами `public.client_keys` и `public.deployment_log`; ORM ownership остаётся раздельным: Management 6, Client 2. Импорта runtime-моделей и третьей линии нет. Для существующей точной схемы сохраняются строки и происхождение `adopted`; partial/drift/plaintext `secret` дают отказ без DROP/UPDATE секретов. Автоматического сопоставления проектов/выдачи прав нет. Downgrade созданной схемы берёт ACCESS EXCLUSIVE обеих таблиц и отказывает при данных; adopted-таблицы сохраняет.

Core revision устанавливает shared transaction advisory barrier на изменения шести таблиц: users, projects, workspace_members, domain_authorizations, crawl_results, recommendation_versions. Statement triggers покрывают пустые UPDATE/DELETE и TRUNCATE, row triggers клонируются на partitions; прямой доступ к восьми crawl partitions также покрыт statement triggers. Management после Task lock берёт exclusive barrier и делает свежие SELECT. Ему не требуется UPDATE на Core authority, который требовался прежним row/table SHARE locks. Все producer transactions завершаются до авторитетной проверки; после точки begin решение in-flight заморожено. Барьер общий, measured capacity этим не заявляется.

Обязательна изоляция READ COMMITTED: runtime engine задаёт её явно, authority отклоняет REPEATABLE READ. Startup/readiness и сами authority-команды проверяют head и включённые parent/partition triggers. Общий lock timeout Management — 5 секунд, включая первый Task lock. PostgreSQL lock timeout/deadlock/serialization conflict (`55P03`, `40P01`, `40001`) откатываются и дают HTTP409 `hitl_concurrency_conflict` без SQL/params; остальные DB errors не объявляются повторяемой гонкой. Потеря соединения/ответа не запускает автоматический apply.

Новые доказательства: `test_runtime_foundation_postgres.py` проверяет создание/точное legacy adoption, отказ drift/plaintext, безопасный downgrade, SELECT-only readiness/event tracking, DDL/stamp/membership UPDATE denial, блокировку direct partition TRUNCATE/role write и отказ при disabled trigger. `test_management_execution_postgres.py` добавляет REPEATABLE READ denial и реальное ожидание через pg_locks: producer меняет role либо вставляет v2, begin ждёт, после producer commit читает новое состояние и отказывает, сохраняя ready/approved без CMS.

`tests.wordpress.app_http` дополнительно удерживает настоящий shared lock и проверяет HTTP409 после timeout; проверяет неавторизованный запрос, refresh вместо access, чужой actor, изменённый hash/payload, отсутствие approval/ownership и запрет публикации при потере DEPLOY. «Одобрить» оставляет CMS неизменной; отдельный apply меняет exact reviewed title один раз; тот же operation ID возвращает тот же результат. Итог `applied_unverified` не подменяет verify.

Audit/Semantic startup используют read-only Core gate; `SEMANTIC_AUTO_CREATE_TABLES` больше не разрешает DDL. Event resilience только читает migrated tracking tables и отказывает при их отсутствии. Старые runtime create statements удалены; запуск с owner/superuser/CREATE-правами не принимается за безопасный runtime.


## Итоговые результаты завершающего инкремента

Проверена рабочая ревизия, поставляемая вместе с этим документом; её идентификатор — commit документа поверх `d4810dd` (доступен через `git log -- docs/AR4_ACCEPTANCE.md`). Среда Windows / Python3.11.9, Docker Desktop Linux containers. Management runtime/test lock: SQLAlchemy2.0.55/Pydantic2.5.3; Client/API — собственные locks с SQLAlchemy2.0.25/Pydantic2.14.2; Audit/Semantic — собственные locks с SQLAlchemy2.0.36/Pydantic2.10.5. Объединения runtime manifests нет.

| Проверка | Результат и среда |
| --- | --- |
| Management unit | **149 passed**, management-test lock |
| Client unit, включая runtime DDL regression | **12 passed**, client-test lock |
| API / Audit / Semantic unit | **518 / 80 / 534 passed**, отдельный test-lock каждого сервиса |
| Management migrations / canonical / approvals / outbox / execution / runtime foundation | **91 passed**, точный management-postgres CI Python-шаг + RequiredTests (skip/xfail = failure), собственный PostgreSQL15 Alpine |
| Core и AL-5 migrations, legacy/data preservation, ORM parity и version repository | **59 passed**, собственный PostgreSQL15; отдельный audit-test runtime + закреплённые Alembic/Mako/MarkupSafe/email-validator/dnspython из management-test как integration tools |
| Audit evidence finalization и реальные Audit/Semantic initializers | **2 passed**, те же изолированные PostgreSQL/asyncpg и integration tools; readonly LOGIN роль, все SQL initializer/event readiness — SELECT |
| Native WP + service authority + actual HTTP apps | **PASS**, `--with-management --with-apps`: настоящие PostgreSQL, Redis7, RabbitMQ3.12, WordPress6.8.3/PHP8.3 и MariaDB10.11; три собранных приложения, UID10001/read-only FS, отдельные no-DDL роли |
| Platform regressions | **96 passed**, включая inventory нового Client startup regression |
| Strict mypy / Ruff / whitespace | **PASS**, migration/runtime gate, authority/executor, platform и HTTP fixture; diff проверен |

Для Core/Audit native integration runtime-пакеты взяты только из audit-test lock; пять дополнительных tools также устанавливались с проверкой hashes, без изменения runtime-пакетов. Отсутствующий email-validator сначала дал отказ импорта Core model, после явной подготовки integration environment оба набора пройдены полностью. Это не пропуск проверки и не системный Python.

История исправления последнего дефекта: реальная блокировка в HTTP stand сначала воспроизвела **RED: ожидался409, получен500**. После rollback + ограниченного SQLSTATE mapping тот же сценарий дал **GREEN409**, затем обычный просмотр/approve/apply успешно завершился. Новые native role/version races подтвердили ожидание advisory lock и свежий отказ после producer commit; итоговые Task/operation остались APPROVED/ready. Заявления о полном наборе мутаций RED нет.

Независимое read-only source review завершающего diff не нашло блокеров; reviewer отдельно проверил barrier/permissions/isolation, миграции и HTTP409. Reviewer не запускал тесты; фактические результаты выше получены автором. Предыдущие frontend **10 passed** + TypeScript/ESLint/build и PHP standalone результаты относятся к инкременту `d4810dd`; frontend/PHP source в завершающем инкременте не менялся. Существующие Pydantic/FastAPI/AnyIO/pytest-asyncio deprecation warnings не скрывались.

Это локальная приёмка AR-4/AC-07 и основание открыть PR. GitHub Actions проверяет опубликованный commit отдельно; все обязательные checks и требуемое ревью владельцев остаются условиями слияния. Полные AC-08/09, самостоятельный verify/rollback, browser/staging E2E, общие grants/deploy/restore и релиз этим статусом не принимаются.
