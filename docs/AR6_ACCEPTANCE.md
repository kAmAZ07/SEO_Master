# AR-6: операции Gateway и восстановление неизвестного результата

Статус: recovery-инкремент реализован и проверен; **AR-6 целиком ещё не закрыт**. База: `4a6dd473f098ea4ff7670838228d6e712d3be935`, ветка `arseniy/ar-6-reconciliation`. Требования: REQ-HITL §10.2, REQ-VERIFY §12.1–12.2; части AC-08/AC-09, относящиеся к исполнению и сверке операции. Независимый public verify, rollback и полный browser E2E относятся к AR-7/AR-9 и этим документом не принимаются.

## Объём

Развивается существующий `ExecutionService` и единственный `HITLExecutor`. Frozen record, operation ID, hash, approval и exact before/after сохраняются. Management владеет состоянием и журналом попыток, Client Gateway выполняет CMS transport. Нового пути apply нет.

После неизвестного результата выполняются только CMS status/receipt запросы. Попытки ограничены, backoff и leases сохраняются в БД. Рестарт Gateway не обнуляет бюджет. Потеря или отсутствие receipt не разрешает новый operation ID или повтор apply. Исчерпание бюджета даёт `needs_reconciliation`, сохраняя запрет повторной публикации.

Фоновый recovery Client Gateway использует service/executor identity только для сверки уже отправленных операций. Ему не передаётся пользовательский JWT и он не одобряет и не применяет изменения. Пользовательские команды продолжают проверять original session и object scope. Положительный подписанный receipt даёт `applied_unverified`/`no_op`, не `verified`.

## Проверки приёмки

| Требование | Необходимое доказательство | Текущий статус |
| --- | --- | --- |
| Стабильные ID/hash и единственный executor | `test_management_execution_postgres.py`, `test_client_gateway_rollback.py`; native HTTP stand | Пройдено: frozen record сохранён, второй apply отсутствует |
| Ограниченные попытки, backoff, ручной выход | `test_management_recovery_postgres.py`: concurrent claim, бюджет, lease expiry, stale completion, новые Session | Пройдено на PostgreSQL, обязательный CI включает модуль |
| Восстановление без пользовательской сессии | `test_management_recovery.py`, `test_client_recovery.py`, native `app_http.py` | Пройдено: обе service keys обязательны, actorless begin запрещён; после SIGKILL recovery успешен при inactive actor |
| Сохранность схемы/данных | Revision `20261114_005_recovery`; native upgrade/backfill, downgrade race/refusal, schema gate | Пройдено; рабочая/общая БД не мигрировалась |
| Реальный CMS и потерянный ответ | `tests.wordpress.native_contract --with-management --with-apps` | Пройдено: настоящий commit WordPress → SIGKILL Gateway → restart → status/receipt; одна WP ledger-запись, прежние ID/hash |
| Отображение неизвестного исхода | `HITL.test.tsx`, `hitlAPI.test.ts`; scoped Management summary | Пройдено: счётчик/причина, запрет нового apply при manual; lease ID отсутствует в публичном summary |

Разрешённые области: Management/Client, их migrations/config, shared wire types, принадлежащий Арсению HITL UI/API, профильные tests/CI и русскоязычная документация. Общий Compose `up/e2e`, рабочая БД и deployment не запускаются обходом существующих gates. Применение схемы проверяется только на собственных одноразовых БД.

## Контракт восстановления и эксплуатация

Бюджет фиксирован в policy v1: максимум 4 CMS status-попытки, интервалы 10/60/300 секунд после неудачного завершения, lease 60 секунд, общий deadline 900 секунд от `begin`. CMS transport ограничен 10 секундами. До каждого HTTP запроса Management транзакционно сохраняет попытку и lease. Падение процесса расходует попытку; по истечении lease backoff считается от его исходного срока, а не от момента нового запуска. Повтор пользователя использует тот же claim/budget, что и фоновый процесс.

Запись операции остаётся в `public.management_changelog`; `metadata.recovery` содержит журнал. Новых таблиц/очереди apply нет. Миграция005 сохраняет старые frozen command/hash/before/after и backfill делает только для `applying`/`outcome_unknown` от исходного `began_at`. Истёкшая старая операция не получает новый срок. Терминальные старые записи не меняются. DB trigger запрещает сокращение/перезапись завершённого префикса журнала; downgrade под ACCESS EXCLUSIVE отказывает при recovery-данных, чтобы последующий upgrade не обнулил бюджет. UTC backfill не зависит от timezone сессии мигратора.

Внутренние `POST /internal/executions/recovery/claim` и `POST /internal/executions/recovery/{operation_id}/report` требуют `X-Internal-API-Key` и `X-Executor-Key`, но не пользовательский JWT. Claim разрешён только после `begin`; lease не разрешает CMS apply. Устаревший report отклоняется без изменения журнала. Поздний проверенный initial receipt может уточнить `needs_reconciliation`; поздний `finish(null)` не сбрасывает результат/бюджет/lease. Receipt проверяется на схему, подпись отдельным ключом и точную привязку к command/request hash и snapshots.

Client запускает одну lifespan-задачу опроса каждые 5 секунд. `EXECUTION_RECOVERY_ENABLED=true` по умолчанию; нужны обе настроенные service keys длиной ≥32. Enabled с ненастроенными ключами/неработающей задачей даёт readiness503. Явный `false` отключает автоматический опрос; такой режим не является доказательством автоматического recovery. Несколько Client экземпляров безопасно делят lease через Management; бюджет общий, а не на процесс.

Ошибки различаются: `cms_unavailable`, `operation_not_found`, `receipt_invalid`, `integration_changed`, отдельно `lease_expired`; причины остановки — `attempts_exhausted`/`deadline_exceeded`. UI объясняет их по-русски. В `needs_reconciliation` Task остаётся IN_PROGRESS, действуют version fence и запрет повторного apply. Ручной выход означает видимый обязательный разбор: автоматического force/reset/new ID или небезопасной кнопки «повторить запись» нет. Сверка CMS подтверждает запись, но не публичную видимость страницы.

## Среда и фактические прогоны

Проверки выполнены на Windows/Docker Desktop `desktop-linux`, Python3.11.9 в отдельных lock-окружениях Management/Client/API; Node24.14.1. Проверена рабочая ревизия поверх указанной базы. PostgreSQL15, WordPress6.8.3/PHP8.3, MariaDB10.11, Redis7/RabbitMQ3.12 закреплены digest в native fixture. Все Docker/DB ресурсы одноразовые, собственные, с проверкой owner label перед очисткой.

- Management unit-профиль: 154 passed; Client: 35 passed; API: 542 passed, без обязательных skip/xfail.
- Полный Management PostgreSQL CI-набор: 103 passed; после ревью итоговый recovery-модуль отдельно — 15 passed, включая concurrent downgrade, поздний receipt из manual, schema trigger gate и UTC backfill. Набор CI не ослаблен.
- Frontend: 207 passed, tsc, ESLint и production build прошли; после API-normalization regression повторён затронутый набор — 9 passed.
- Strict mypy: новая migration line — 10 файлов с реальными зависимостями; изменённый typed execution/recovery контур — 16 файлов с `--follow-imports=silent` для нетронутого legacy. Ruff и `git diff --check` проходят.
- Native WordPress/authority/HTTP stand прошёл полностью, включая настоящий SIGKILL после CMS commit, восстановление и проверку одной ledger-записи. Это не browser/staging E2E и не AR-7 verify/rollback.

Независимое read-only ревью выявило гонку downgrade (check до table lock) и ложный readiness при enabled с неверными keys. Оба исправлены, добавлены регрессии. Платформенные проверки: 97 passed; для проверки ACL собственных временных каталогов прогон выполнен вне Windows sandbox. Предупреждения deprecated API Pydantic/Starlette/Vite/Browserslist и размер frontend chunk не устранены этим инкрементом.

## Что осталось до полного AR-6

REQ-VERIFY §12.1 требует наблюдаемые `queued → preflight/dry_run → ready` и допубликационные исходы `unsupported`, `conflict`, `failed_before_apply`. Сейчас первый preflight выполняется до reserve, а отказ второго preflight/dry-run оставляет запись `ready` без сохранённой причины. Следующая часть AR-6 должна сохранить эти фазы/отказы в авторитетном Management state и покрыть гонки/рестарт до отправки write. Нельзя записывать `failed_before_apply` после неопределённого write dispatch: там остаётся `outcome_unknown` и этот status-only контур.

До устранения этого остатка весь AR-6 не считается выполненным, PR для его полного принятия не создаётся. Public verify/rollback, их deadline/drift и пользовательские действия — последующий AR-7; полноценный экран receipt/rollback — AR-8; browser E2E — AR-9.
