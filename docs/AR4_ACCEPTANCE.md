# AR-4: реализация и доказательство приёмки

Дата: 09.11.2026. База инкремента: `ec317c40301be25aa8c8139ad0fb69010fac40c6`, ветка `arseniy/ar-4-score-handoff`. Проверяется рабочая ревизия этого инкремента; итоговый commit определяется Git. Требование: REQ-HITL §10, primary AC-07; стыки с §3.2/5.2/7/11/12. AL-5 используется из реального `RecommendationVersion`, отдельной копии схемы нет.

## Текущее состояние

Реализован и проверен native путь: сохранённая версия/evidence → явное человеческое решение → отдельная команда → Management authority → единственный Gateway executor → реальный WordPress → подписанный receipt → `applied_unverified`. Неподготовленная/неодобренная версия не достигает CMS. Одобрение само CMS не меняет.

**Полное эксплуатационное закрытие AR-4/AC-07 пока не объявляется:** общий AR-3 migration/startup gate остаётся закрыт. Client startup выполняет `create_all/ALTER/UPDATE/DROP COLUMN secret`, Audit startup также содержит DDL; полные приложения ещё нельзя безопасно поднять штатной ролью `seo_runtime`. Native integration вызывает настоящие service-функции с реальными БД/CMS, но не lifespan приложений. Нужны завершение этого миграционного стыка и проверка маршрутов запущенных приложений без DDL-прав. `up/e2e`, рабочая БД и production-публикация этим инкрементом не разрешаются. PR в main до полного закрытия не создаётся.

Verify/rollback и полная модель доставки AR-6/7, browser/staging сценарий AR-9, совместимость тем и выпуск плагина AR-15/17 остаются отдельными задачами. Receipt не означает `verified`.

## Авторитет и состояния

- Management владеет approvals и создаёт frozen DeploymentOperation через `ExecutionService.reserve`. Используются зарезервированные строки существующего `management_changelog`, без седьмой ORM-таблицы. `record` содержит ссылки, project/workspace, actor/role, decision hash, полную AL-5 версию, server binding, exact before/after и CMS command/request hash.
- Revision `20261109_003_execution` добавляет partial unique index на task/version и защиту frozen record/переходов/удаления. Текущий Management head — эта revision. Сначала применяется Core, затем явный Management runner; startup читает schema/index/trigger/role, не создаёт их.
- `ready → applying → applied_unverified | no_op | outcome_unknown`. Только неотправленный `ready` можно отменить. Истёкший ready отменяется при явном резервировании нового ID; отправленная или неизвестная операция никогда не возвращается в ready.
- `begin` — точка принятия исполнения: producer-table barrier, fresh Task/Approval/operation locks и проверки после ожиданий. Проверяются original actor/DEPLOY, активный canonical project, approver/APPROVE, версия/hash/target/evidence, решение, domain verification и время. Подготовленная версия и человеческое решение заморожены для in-flight операции; новое решение/late saga failure не заменяет её. Позднее изменение membership относится к будущим операциям.
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
| Actor/session/role/object scope; body identity запрещена | `test_management_approvals.py`, `test_hitl_gateway.py`, native approvals suite | Реальные auth/session unit-пути отдельно; SQL membership/decision locks на PostgreSQL. Нет заявления о browser E2E |
| Immutable version/hash/target/evidence, TTL24h, revoke | `test_management_approvals_postgres.py`, `test_management_outbox_postgres.py` | Настоящие transactions, refresh старого identity map, approve/reject races, history/outbox atomicity, regeneration |
| Единственный executor, frozen payload, Task lifecycle | `test_hitl_handler.py`, `test_management_proposals.py`, `test_management_execution_postgres.py` | Legacy writers отказывают до DB/CMS, PATCH не задаёт system status/metadata; DB immutable operation/unique version; конкурентный begin — один победитель |
| Отказы до первой CMS-мутации | native execution suite + `tests.wordpress.native_contract` | PostgreSQL: expiry/revoke/role/domain/actor/before/hash. Настоящий WordPress: URL/CAS/deadline/filter отказы, истечение во время реального SQL lock wait; SEO/ledger не изменены |
| Replay/неизвестный результат | `test_client_gateway_rollback.py`, native execution и WordPress suites | Инжектированная потеря apply response → unknown → status only; настоящий WP: два параллельных same-op HTTP → одинаковый signed receipt и одна ledger-операция. Не process-kill test |
| Human record → outbox → inbox | native outbox и execution suite | Exact envelope/history, durable processed_events, commit до двух ACK, forged payload отвергнут, CMS/operation count=0. Предыдущий RabbitMQ suite отдельно проверял publisher, не полный новый consumer-restart сценарий |
| Просмотр → одобрение → отдельное применение | frontend/API suites; native authority integration | UI/API references и отдельные действия; фактические Core+Management migrations, AL-5 payload/evidence, approval, Gateway, mysqli и receipt связаны одним native сценарием. Audit facts/user/domain используются как явно подготовленные fixtures |
| Упаковка | Client и Management Docker build/import probes | Linux images с собственными runtime locks, `shared/` присутствует, main imports/execution routes/head и UID10001 подтверждены без сети; lifespan/startup DDL не выполнялся |

Локальные результаты: Management unit **149 passed**; Client unit **11 passed**; PostgreSQL migration/approval/outbox/execution **79 passed**; API **518 passed**; frontend **10 passed**, production build, TypeScript и ESLint; окончательный полный platform suite **96 passed**, включая bootstrap upgrade. Профили используют собственные locks: Python3.11.9, Management SQLAlchemy2.0.55/Pydantic2.5.3, Client/API SQLAlchemy2.0.25/Pydantic2.14.2; Node24.14.1.

PHP standalone: syntax17, legacy8/8, protocol41/41, adapter38/38, scripted store35/35 и shared Python/PHP vectors. Эти цифры не заменяют настоящий WordPress. MariaDB `bigint(20) unsigned` дефект сначала воспроизведён падающим scripted regression, затем исправлен точным признанием того же unsigned64 типа; signed тип по-прежнему отвергается.

Native command из окружения с закреплённым `locks/management-test.txt`:

```text
python -B -m tests.wordpress.native_contract --docker-context desktop-linux --with-management
```

Linux/CI использует `--docker-context default`. Образы WordPress/MariaDB/PostgreSQL закреплены digest в `tests/wordpress/native.py`; создаются собственные containers/network/tmpfs и случайные credentials. Перед очисткой проверяется owner label. Application config импортируется из пустого cwd с очищенным окружением. Native WP требует strict SQL, явно настроенный только для fixture; guard production storage не ослаблен. Native job добавлен в CI; локальный pass не означает уже прошедший GitHub Actions run.

Независимый read-only reviewer проверил safety diff. Замечания о permalink/Options filters, legacy lifecycle writers, packaging, env injection и stable UI retries исправлены; автор отдельно проверил изменения и приведённые результаты. Набор «все 31 мутация RED» не заявляется.
