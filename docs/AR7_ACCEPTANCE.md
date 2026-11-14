# AR-7: независимый public verify и безопасный rollback

Статус: реализация и локальные gates AR-7 завершены в описанном объёме; формальное принятие primary AC-09 и полный CI не подменяются локальными прогонами. База `2f2c5a847096627a0359b92864a306f39246683c` (AR-6 / PR108 в main), ветка `arseniy/ar-7-verify-rollback`. Требования REQ-VERIFY §12.3–12.4, primary AC-09; стыки REQ-HITL §10.2 и REQ-WP §11.2–11.4. AR-8 полный UI и AR-9 browser/staging E2E отдельно.

## Контракт

Management остаётся владельцем состояния, Client Gateway — единственным CMS executor. Развивается существующая операция в `management_changelog`, новые таблицы и независимые пути apply не вводятся. Тип apply (включая legacy без поля type) или rollback неизменяем; rollback ссылается на authoritative original operation/receipt/hash/binding и точные snapshots. Caller передаёт original ID, новый operation ID и reason; не присылает before/after или receipt. Старый approval не разрешает повторный apply. Явный rollback проверяет текущие DEPLOY, scope/ownership и собственный короткий deadline перед первой мутацией.

Публичное чтение независимо от CMS/admin-клиента: SafeHttpClient с DNS/connect/TLS policy, без cookie/Authorization, без redirects, 1MiB/10sec; фиксированные anonymous headers. Сохраняются URL/UTC time, HTTP/cache headers, extract, body hash и per-field differences. HTML не исполняется, JSON-LD contexts не загружаются; сравниваются decoded title/description и canonical JSON-LD, не весь динамический HTML. Missing, null и empty различаются. Дубли/invalid/unsupported predicates не дают verified. Managed H1/links остаются unsupported/manual до соответствующего CMS-контракта AR-12.

До write сохраняется неизменяемый публичный baseline: он нужен, чтобы rollback отсутствующего/пустого managed поля можно было проверить с учётом fallback темы и сторонних JSON-LD блоков. Baseline нельзя дописать после begin/unknown или пересчитать существующий legacy record/hash. При отсутствии нужного baseline старой операции проверка честно требует данных, не заявляет rollback_verified по CMS receipt.

Verify deadline по умолчанию 300sec фиксируется от первого durable сохранения подтверждённого receipt в Management; попытки/lease/backoff сохраняются, restart/status reconciliation не обнуляет их. При stale CDN — verifying/pending с differences, затем bounded retry; окончательный mismatch/deadline — verification_failed. Verified означает наблюдённое совпадение в момент чтения. Начало rollback сериализуется с original verify и инвалидирует старую lease; поздний ответ не может воскресить verified.

Rollback использует точный original before, сохраняет отсутствие поля, получает собственный signed receipt и независимый verify (`rollback_applied_unverified`/`rollback_verified`). Drift блокирует запись. После неизвестного write результата только status/receipt той же операции, без нового ID/force. Автоматического rollback без заранее согласованной политики нет.

## Необходимые доказательства

| Требование | Проверка | Статус |
| --- | --- | --- |
| Anonymous safe public fetch и predicates | `test_public_verifier.py`, Client profile | Пройдено: 61 tests всего профиля; private DNS, отсутствие credentials, missing/empty, inert HTML, invalid/duplicate JSON-LD, XHTML fail closed, worker lease-before-fetch |
| Durable verify без reset | `test_management_verification_postgres.py` + recovery/preparation/runtime modules | Пройдено: 57 PostgreSQL tests выбранного набора, включая mismatch→match, deadline/restart, concurrent claim, late report после rollback |
| Текущие права, linked rollback и точный before | PostgreSQL roles/approval expiry/idempotency/drift; PHP contracts | Пройдено: expired historical approval не мешает текущему DEPLOY; новый Task не повреждается; конкурирующий apply блокируется до receipt |
| Потерянный ответ при записи | `tests.wordpress.native_contract --with-management --with-apps` | Пройдено на WP6.8.3/PHP8.3/MariaDB10.11/PG15: apply и rollback после настоящего SIGKILL, service-only recovery при inactive user, ровно один receipt каждой операции, независимый public verified/rollback_verified |
| Migration preservation и readonly startup | Обязательный Management PostgreSQL gate, SQL007 и native app readiness | Пройдено: 139/139 PostgreSQL tests всего обязательного набора, новый head007, immutable record/baseline/receipt/journal и downgrade refusal |
| Публичные scoped API/UI | API profile + HITL Vitest/tsc/eslint | Пройдено: 547 API tests, HITL 14/14; Task перечитывается с сервера, optimistic verified отсутствует |

Рабочие/общие БД не мигрировались. Использовались только собственные labelled temporary resources с проверкой владельца при очистке; обязательные gates `up/e2e` не обходились. Независимое read-only review нашло и помогло исправить inert HTML false proof, stale approval identity map, legacy JSON-LD error path, cross-version Task corruption и UI cancellation/UTF-8 limits; регрессионные тесты добавлены. Полный CI остаётся условием слияния.

## API и эксплуатация

- `POST /api/hitl/tasks/{task_id}/executions/{original_id}/rollback`: строго `{operation_id, reason}`; текущий DEPLOY и object scope, новый UUID и непустая причина до 1024 UTF-8 bytes. Management берёт оригинальные receipt/hash/binding/before сам. Смена reason с тем же ID даёт conflict. Никакого `force` или caller inverse payload.
- Существующий scoped `.../executions/{operation_id}/cancel` отменяет и rollback **до begin**; UI отменяет именно rollback ID и выдаёт новый ID только после подтверждённой отмены. При applying/unknown/manual ID сохраняется, доступен status-only reconciliation.
- Внутренние `/internal/executions/verification/claim` и `/verification/{id}/report` требуют обе service identities, не user JWT. Попытка/lease сохраняются до публичного HTTP; Management пересчитывает predicates из frozen version, caller `matches` не является доказательством.
- `VERIFICATION_DEADLINE_SECONDS=300`, диапазон 30…3600; 8 попыток, lease30sec, backoff5/10/20/40/60/60/60. Значение фиксируется на операции, config/restart его не обнуляет. Existing receipts без policy получают старый `finished_at`/signed `applied_at`, не новый бюджет. Общий Client worker обслуживает status recovery и public verify; `EXECUTION_RECOVERY_ENABLED=false` явно отключает оба.
- Management schema head007, Core head прежний. Readiness проверяет guard и оба уникальных индекса. Downgrade под ACCESS EXCLUSIVE отказывает при сохранённой verification/baseline/rollback истории. Startup DDL отсутствует; разворачивать Management/Client согласованно.

Native HTTP acceptance проверяет полный путь для title. Missing/empty description и многоблочный JSON-LD покрыты unit/PHP contracts; это не доказательство всех тем/Yoast/RankMath/CDN. Только `text/html` поддержан public parser v1; XHTML, неоднозначная структура и неподдержанные predicates не дают verified. Реальный CDN и browser/staging E2E остаются отдельными уровнями приёмки. ZIP/dist плагина не пересобирался, RC/release не создавался.

## Воспроизведение проверок

Использовать Python3.11 и отдельные `locks/management-test.txt`, `locks/client-test.txt`, `locks/api-test.txt`; системный Python3.14 не подходит платформенному gate. Команды: `scripts/backend.py check {management,client,api}`, `python -m unittest discover -s tests/platform -v`, `python -m tests.wordpress.run --php <php8.3>`, `python -m tests.wordpress.native_contract --docker-context <local> --with-management --with-apps`, frontend `npm test`, `npx tsc --noEmit`, `npm run lint`, `npm run build`. PostgreSQL gate и strict/ruff команды заданы в `.github/workflows/ci.yml`; обязательный skip/xfail не считается pass.
