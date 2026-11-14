# AR-6: операции Gateway и восстановление неизвестного результата

Статус: реализация AR-6 завершена и проверена локально; **слияние требует полного зелёного обязательного CI**. База: `4a6dd473f098ea4ff7670838228d6e712d3be935`, recovery-инкремент `a99e3cff20e652617d5a6e4d57f1890f7d5470d4`, ветка `arseniy/ar-6-reconciliation`. Требования: REQ-HITL §10.2, REQ-VERIFY §12.1–12.2; части AC-08/AC-09, относящиеся к исполнению и сверке операции. Независимый public verify, rollback и полный browser E2E относятся к AR-7/AR-9 и этим документом не принимаются.

## Объём

Развивается существующий `ExecutionService` и единственный `HITLExecutor`. Frozen record, operation ID, hash, approval и exact before/after сохраняются. Management владеет состоянием и журналом попыток, Client Gateway выполняет CMS transport. Нового пути apply нет.

После неизвестного результата выполняются только CMS status/receipt запросы. Попытки ограничены, backoff и leases сохраняются в БД. Рестарт Gateway не обнуляет бюджет. Потеря или отсутствие receipt не разрешает новый operation ID или повтор apply. Исчерпание бюджета даёт `needs_reconciliation`, сохраняя запрет повторной публикации.

Фоновый recovery Client Gateway использует service/executor identity только для сверки уже отправленных операций. Ему не передаётся пользовательский JWT и он не одобряет и не применяет изменения. Пользовательские команды продолжают проверять original session и object scope. Положительный подписанный receipt даёт `applied_unverified`/`no_op`, не `verified`.

## Проверки приёмки

| Требование | Необходимое доказательство | Текущий статус |
| --- | --- | --- |
| Стабильные ID/hash и единственный executor | `test_management_execution_postgres.py`, `test_client_gateway_rollback.py`; native HTTP stand | Пройдено: frozen record сохранён, второй apply отсутствует |
| Операция до первого CMS I/O, наблюдаемые ранние стадии | `test_management_preparation_postgres.py`, `test_client_preparation.py`, native HTTP stand | Пройдено: intent и SQL NULL snapshots в queued; preflight → dry_run → ready; один freeze |
| Предпубликационный отказ и отмена | Native конкуренция cancel/begin, lease после Task lock, старая/новая версия; API/UI tests | Пройдено: rejected/unsupported/conflict/failed_before_apply, явная отмена, Task не перезаписывается readonly callback |
| Ограниченные попытки, backoff, ручной выход | `test_management_recovery_postgres.py`: concurrent claim, бюджет, lease expiry, stale completion, новые Session | Пройдено на PostgreSQL, обязательный CI включает модуль |
| Восстановление без пользовательской сессии | `test_management_recovery.py`, `test_client_recovery.py`, native `app_http.py` | Пройдено: обе service keys обязательны, actorless begin запрещён; после SIGKILL recovery успешен при inactive actor |
| Сохранность схемы/данных | Revisions `20261114_005_recovery`, `20261114_006_preparation`; native upgrade/backfill, downgrade race/refusal, schema gate | Пройдено: active recovery и terminal receipts005 сохранены без переписывания; рабочая/общая БД не мигрировалась |
| Реальный CMS и потерянный ответ | `tests.wordpress.native_contract --with-management --with-apps` | Пройдено: SIGKILL до freeze и после dry-run — ноль мутаций управляемых полей; после настоящего commit — status/receipt и одна мутация |
| Отображение неизвестного исхода | `HITL.test.tsx`, `hitlAPI.test.ts`; scoped Management summary | Пройдено: счётчик/причина, запрет нового apply при manual; lease ID отсутствует в публичном summary |

Разрешённые области: Management/Client, их migrations/config, shared wire types, принадлежащий Арсению HITL UI/API, профильные tests/CI и русскоязычная документация. Общий Compose `up/e2e`, рабочая БД и deployment не запускаются обходом существующих gates. Применение схемы проверяется только на собственных одноразовых БД.

## Подготовка до write и совместимость

Новый Gateway сначала создаёт `queued` через Management. В той же `management_changelog` строке `metadata.intent` фиксирует operation/task/approval/version/hash, canonical project/workspace, original actor/role, decision hash и полную AL-5 версию. До snapshot `record == intent`, `record_hash` хеширует intent, `intent_hash` сохраняется навсегда; `before_value` и `after_value` — SQL NULL, а не `{}` и не выдуманный снимок. Существующий unique index task/version действует сразу, поскольку version ID есть на прежнем уровне `record` уже в queued.

Один preparation lease охватывает readonly preflight → snapshot → freeze → dry-run → ready. Он сохраняется до CMS I/O, действует не более 60 секунд; весь пользовательский запрос ограничен 900 секундами и четырьмя попытками подготовки. Журнал событий дописывается, старый префикс/бюджет/срок нельзя переписать. Активная последняя попытка не отменяется дополнительным claim: конкурент получает409. Рестарт до write не запускает публикацию автоматически; следующий явный apply продолжает тот же ID после окончания прежнего lease.

После snapshot `reserve` один раз дополняет frozen record привязкой CMS, exact before/after, command и request hash; состояние становится `dry_run`. Версия, intent и actor не меняются. Перед freeze заново проверяются approval/version/evidence/права/ownership и свежий lease после ожидания блокировок. Затем Gateway выполняет readonly dry-run и сообщает готовность. `begin` ещё раз проверяет права, точную версию, lease и неизменённый срок команды. Перед CMS write Gateway требует `begin.state == applying`, прежний canonical JSON record и record hash. Потеря/подмена ответа begin не разрешает apply.

После freeze никакая новая попытка не пересоздаёт command/request hash/authorization deadline. При изменившемся snapshot — `conflict`; после истечения срока — `failed_before_apply/authorization_expired`. CMS-неподдерживаемость даёт `unsupported`, потеря одобрения/прав — `rejected`, остальные известные readonly сбои — `failed_before_apply`. `authority_unavailable` отличается от `cms_unavailable`. Эти исходы означают отсутствие отправки write; они не ставят Task FAILED и не меняют новую approval/version. Невалидное согласование до enqueue даёт отказ HTTP и не создаёт разрешённую операцию.

Отменить можно только ещё не начатую операцию. `POST /api/hitl/tasks/{task_id}/executions/{operation_id}/cancel` проверяет исходную сессию, DEPLOY и task/project binding; Management сериализует cancel с begin через Task → operation locks. Cancel-first запрещает старым callbacks/lease продолжить write; begin-first даёт cancel409. Сохраняются actor, UTC time и причина отмены. Явная отмена доказанно неотправленного запроса освобождает version fence для нового ID; `applying`/`outcome_unknown`/`needs_reconciliation` так освободить нельзя. UI показывает исходы и отдельную кнопку отмены, не повторяет apply при неизвестном результате.

Внутренний протокол добавляет `/queue`, `/{id}/preparation/claim|ready|fail`, `/{id}/cancel`; reserve/begin требуют preparation lease. Claim и cancel требуют user actor; readonly ready/fail callback требует обе service keys и matching live lease. Callback после cancel/новой lease/begin не понижает состояние. Публичный operation/task summary содержит phases, причины и счётчик, но не lease token. HTTP200 с ресурсом `conflict`/`failed_before_apply` не означает публикацию.

Migration006 не меняет старые record/hash/snapshots/receipts/recovery. Legacy ready можно продолжить только readonly preparation с тем же frozen command; старый reserve больше не создаёт новые old-format ready операции без queue. Миграция разрешает ровно один intent→frozen переход, защищает journal и отказывает в downgrade при истории подготовки под ACCESS EXCLUSIVE. Текущий Management head — `20261114_006_preparation`, Core head прежний `20261109_001_hitl_barrier`. Обновлять согласованно Management/Client; readiness требует новый head, startup DDL не добавлен. Публичные существующие `/api` apply/status сохраняются; новый supported transport использует `/seo-master/v2/target-bound/*`, а старые write-пути и `/schema/schema` не являются обходом executor.

## Контракт восстановления и эксплуатация

Бюджет фиксирован в policy v1: максимум 4 CMS status-попытки, интервалы 10/60/300 секунд после неудачного завершения, lease 60 секунд, общий deadline 900 секунд от `begin`. CMS transport ограничен 10 секундами. До каждого HTTP запроса Management транзакционно сохраняет попытку и lease. Падение процесса расходует попытку; по истечении lease backoff считается от его исходного срока, а не от момента нового запуска. Повтор пользователя использует тот же claim/budget, что и фоновый процесс.

Запись операции остаётся в `public.management_changelog`; `metadata.recovery` содержит журнал. Новых таблиц/очереди apply нет. Миграция005 сохраняет старые frozen command/hash/before/after и backfill делает только для `applying`/`outcome_unknown` от исходного `began_at`. Истёкшая старая операция не получает новый срок. Терминальные старые записи не меняются. DB trigger запрещает сокращение/перезапись завершённого префикса журнала; downgrade под ACCESS EXCLUSIVE отказывает при recovery-данных, чтобы последующий upgrade не обнулил бюджет. UTC backfill не зависит от timezone сессии мигратора.

Внутренние `POST /internal/executions/recovery/claim` и `POST /internal/executions/recovery/{operation_id}/report` требуют `X-Internal-API-Key` и `X-Executor-Key`, но не пользовательский JWT. Claim разрешён только после `begin`; lease не разрешает CMS apply. Устаревший report отклоняется без изменения журнала. Поздний проверенный initial receipt может уточнить `needs_reconciliation`; поздний `finish(null)` не сбрасывает результат/бюджет/lease. Receipt проверяется на схему, подпись отдельным ключом и точную привязку к command/request hash и snapshots.

Client запускает одну lifespan-задачу опроса каждые 5 секунд. `EXECUTION_RECOVERY_ENABLED=true` по умолчанию; нужны обе настроенные service keys длиной ≥32. Enabled с ненастроенными ключами/неработающей задачей даёт readiness503. Явный `false` отключает автоматический опрос; такой режим не является доказательством автоматического recovery. Несколько Client экземпляров безопасно делят lease через Management; бюджет общий, а не на процесс.

Ошибки различаются: `cms_unavailable`, `operation_not_found`, `receipt_invalid`, `integration_changed`, отдельно `lease_expired`; причины остановки — `attempts_exhausted`/`deadline_exceeded`. UI объясняет их по-русски. В `needs_reconciliation` Task остаётся IN_PROGRESS, действуют version fence и запрет повторного apply. Ручной выход означает видимый обязательный разбор: автоматического force/reset/new ID или небезопасной кнопки «повторить запись» нет. Сверка CMS подтверждает запись, но не публичную видимость страницы.

## Среда и фактические прогоны

Проверки выполнены на Windows/Docker Desktop `desktop-linux`, Python3.11.9 в отдельных lock-окружениях Management/Client/API; Node24.14.1. Проверена рабочая ревизия поверх указанной базы. PostgreSQL15, WordPress6.8.3/PHP8.3, MariaDB10.11, Redis7/RabbitMQ3.12 закреплены digest в native fixture. Все Docker/DB ресурсы одноразовые, собственные, с проверкой owner label перед очисткой.

- Management unit-профиль: 154 passed; Client: 40 passed; API: 545 passed, без обязательных skip/xfail.
- Полный Management PostgreSQL CI-набор: 125 passed; затем отдельный final preparation-модуль — 22 passed, включая дополнительные authority/rejected/budget regressions. Обязательные проверки recovery не удалены: тест старой005 миграции создаёт настоящий legacy формат на005, не отключает triggers и сохраняет проверку старой downgrade race.
- Frontend: 209 passed, tsc, ESLint и production build прошли; normalizer сохраняет неизвестное `prepared` как отсутствующее значение.
- Strict mypy: migration line — 11 файлов с реальными зависимостями; typed execution/preparation/recovery контур — 19 файлов с `--follow-imports=silent` для нетронутого legacy. Ruff и `git diff --check` проходят.
- Native WordPress/authority/HTTP stand прошёл полностью, включая настоящие SIGKILL до freeze, после dry-run и после CMS commit; до begin нет WP ledger-операции, после commit ровно одна мутация. Это не browser/staging E2E и не AR-7 verify/rollback.

Независимое read-only ревью выявило гонку downgrade (check до table lock) и ложный readiness при enabled с неверными keys. Оба исправлены, добавлены регрессии. Платформенные проверки: 97 passed; для проверки ACL собственных временных каталогов прогон выполнен вне Windows sandbox. Предупреждения deprecated API Pydantic/Starlette/Vite/Browserslist и размер frontend chunk не устранены этим инкрементом.

## Приёмка и следующая задача

Остаток ранних стадий, зафиксированный в `a99e3cf`, реализован. Source review независимого агента не выявил блокирующих safety-дефектов; это отдельный уровень доказательства, агент не запускал тесты и не менял файлы. Требуемый полный CI остаётся условием слияния; локальные прогоны не заменяют проверок PR.

После AR-6 следующий этап — AR-7: независимый public verify через безопасный сетевой контур и rollback с drift/conflict и собственным receipt. `verifying`, `verified`, `verification_failed` и rollback здесь не симулируются. Полноценный экран receipt/rollback — AR-8; browser E2E — AR-9. Полные AC-08/09, исправленный P0-контур и RC1.0 не объявляются принятыми одним закрытием AR-6.
