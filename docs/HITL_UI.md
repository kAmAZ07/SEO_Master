# Интерфейс согласования и просмотра CMS-операций

Требования: REQ-HITL §10, REQ-REPORTS §14.4, AC-07/08/09. Состояния и авторизация принадлежат Management, см. [EXECUTION.md](EXECUTION.md).

## Поведение интерфейса

HITL UI использует существующий механизм approval/apply. Карточка явно показывает ID версии и content hash, соответствующие просматриваемому diff и передаваемые неизменными в решение. Approval и публикация остаются отдельными действиями; отсутствие версии/hash не разрешает решение.

Кнопки «Квитанция и снимки публикации/отката» читают scoped `GET /api/hitl/tasks/{task_id}/executions/{operation_id}`. Открытие и «Обновить данные операции» не вызывают reserve/begin/apply/recovery POST и не расходуют budget public verifier.

Клиент проверяет identity операции, task/project/version/content hash и связь receipt с frozen request/proposal/before/after hashes. Это проверка целостности отображения; браузер не проверяет HMAC самостоятельно и не получает signing secrets. Receipt не превращается в `verified`; состояние и отдельные публичные observations приходят из Management.

Снимки показаны как escaped JSON: missing, null, empty и remove не заменяются пустым объектом. HTML/JSON-LD не исполняется. Для ещё не подготовленной операции отсутствие snapshots обозначается отдельно. При отказе доступа, неверной identity или malformed receipt данные скрываются, доступен повторный GET.

При закрытии/смене операции запрос прерывается; результат, пришедший после abort, игнорируется даже при transport, игнорирующем отмену. Loaded state связан с task/project/version/hash/operation. Redux использует list request ID и generation: POST → boundary → GET detail; boundary инвалидирует более старые list reads, более поздний список может завершиться до/после detail и не заменяется старым detail. Старые list errors игнорируются. Reject тоже перечитывает authoritative карточку и сохраняет её причину, не удаляет task по старому ID.

Профильные проверки: `frontend/src/pages/HITL.test.tsx`, `frontend/src/api/hitlAPI.test.ts`, `frontend/src/components/hitl/hitlState.test.ts` и component tests просмотра операции. Матрица проверок и границы unit/browser acceptance — [TESTING.md](TESTING.md).

## Приоритет очереди

HITL сохраняет порядок страниц Management. Блок «Приоритет» показывает `priority_v1`, impact/confidence/effort, расчётный и effective score, класс/причину срочности, эвристическую природу оценки и действующий override с actor/reason/time. Неполные входы отображаются как unknown, ноль сохраняется. Некорректный или внутренне противоречивый wire-контракт скрывается как недоступный; UI не подставляет балл.

Форма позволяет оценить входы, задать ручной балл или снять его; каждое изменение требует причины и текущей revision. Пустые числовые поля означают null. Backend разрешает запись только owner/admin; ошибка прав, конфликт revision или неизвестный исход не превращаются в локальный success. После подтверждения перечитывается authoritative очередь. Чтение последних записей журнала не вызывает write; история и комментарии отображаются как escaped text. Полная пагинация доступна через [API приоритетов](MANAGEMENT.md).
