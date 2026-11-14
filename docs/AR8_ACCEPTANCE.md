# AR-8: просмотр версии и сохранённых результатов операции

Основание: REQ-HITL §10, REQ-REPORTS §14.4; primary AC-07, связи AC-08/09. База main `73b108fc8235b7318cb90ca6f68127f72e8f8bff`, ветка `arseniy/ar-8-hitl-review`. Реализация описанного UI-инкремента и локальные frontend gates завершены; browser acceptance и формальная приёмка AC не заявляются. Очередь самостоятельной работы: [ARSENIY_PLAN.md](ARSENIY_PLAN.md).

## Инкремент

Развивается существующий HITL UI, без нового механизма approval/apply. Карточка явно показывает ID версии и content hash, соответствующие просматриваемому diff и передаваемые неизменными в решение. Approval и публикация остаются отдельными действиями; отсутствие версии/hash не разрешает решение.

Кнопки «Квитанция и снимки публикации/отката» читают существующий scoped `GET /api/hitl/tasks/{task_id}/executions/{operation_id}`. Дополнительный endpoint, изменение сервисного DTO или миграция не нужны. Открытие и «Обновить данные операции» не вызывают reserve/begin/apply/recovery POST и не расходуют budget public verifier.

Клиент проверяет identity операции, task/project/version/content hash и связь receipt с frozen request/proposal/before/after hashes. Это проверка целостности отображения; браузер не проверяет HMAC самостоятельно и не получает signing secrets. Receipt не превращается в `verified`; состояние и отдельные публичные observations приходят из Management.

Снимки показаны как escaped JSON: missing, null, empty и remove не заменяются пустым объектом. HTML/JSON-LD не исполняется. Для ещё не подготовленной операции отсутствие snapshots обозначается отдельно. При отказе доступа, неверной identity или malformed receipt данные скрываются, доступен повторный GET.

При закрытии/смене операции запрос прерывается; результат, пришедший после abort, игнорируется даже при transport, игнорирующем отмену. Loaded state связан с task/project/version/hash/operation. Redux использует list request ID и generation: POST → boundary → GET detail; boundary инвалидирует более старые list reads, более поздний список может завершиться до/после detail и не заменяется старым detail. Старые list errors игнорируются. Reject тоже перечитывает authoritative карточку и сохраняет её причину, не удаляет task по старому ID.

## Проверки

| Требование → AC | Тест | Доказательство / статус |
| --- | --- | --- |
| Видимая версия/hash → AC-07 | `HITL.test.tsx` approve: видимая версия/hash совпадают с exact references отправленного решения | Пройдено в полном frontend наборе |
| Scoped GET, чужая identity → AC-07/08 | `hitlAPI.test.ts`: task/project/version/hash/op/site/entity/approval substitution, original rollback refs/type/outcome; verified без согласованных public evidence | Пройдено, 32 API-client tests со stub; клиент не рассчитывает verified и не проверяет HMAC сам |
| Exact snapshots, inert display → AC-08/09 | `OperationDetails.test.tsx`: missing/empty/null и HTML как текст | Пройдено, 3 component tests; HMAC/CMS runtime ими не доказывается |
| Гонка чтений → AC-07 | `hitlState.test.ts`, `OperationDetails.test.tsx` | Пройдено: 6 Redux race cases, late detail response игнорируется. До исправления зафиксированы RED первоначальных и дополнительных зеркальных stale-response regressions |
| Нет browser optimistic verified → AC-09 | API-client/apply/rollback fixtures и существующие HITL cases | Receipt остаётся applied_unverified/rollback_applied_unverified; итог приходит с сервера |

Полный frontend: **250/250, 24 файла**, `npx tsc --noEmit`, `npm run lint`, `npm run build` прошли. Локальная среда — Node24.14.1, существующие зависимости по package-lock; команда полного набора `npm test -- --maxWorkers=4`. Первоначальный прогон без ограничения параллелизма упёрся в штатный 5sec timeout холодного импорта неизменённого App.test (последующий тест увидел оставшийся render); отдельно App.test прошёл 3/3. Ограничение workers не меняет corpus/assertions/timeouts. CI должен подтвердить штатный clean-install gate; нестабильность не скрывалась отключением теста/увеличением timeout.

Независимое read-only source review завершено без оставшихся blockers после исправлений wire validation и зеркальных гонок; reviewer не запускал тесты. Проверки backend/PG/WP из PR109 не выдаются за повторный запуск этой дельты: backend не изменялся. Browser against running apps и устанавливаемый WordPress package остаются AR-9/AR-15; эту страницу нельзя использовать как доказательство приёмки всего продукта или всех AC.
