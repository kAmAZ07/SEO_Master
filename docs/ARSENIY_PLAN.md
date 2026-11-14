# План самостоятельной работы Арсения

Зафиксирован по поручению пользователя 14.11.2026. База: main `73b108fc8235b7318cb90ca6f68127f72e8f8bff`, PR109 смержен. AR-4/6/7 дают проверенный путь HITL → CMS receipt → независимый public verify → явный rollback. Это не закрытие всего ТЗ или RC. CODEOWNERS сохраняет AR-8 за Арсением; предложенная передача Александре не подтверждена.

## Очередь без ожидания новых AL-задач

| Порядок | Задача и требования | Самостоятельный результат и критерий завершения |
| --- | --- | --- |
| 1 | **AR-8**, REQ-HITL §10, REQ-REPORTS §14.4; primary **AC-07**, связи AC-08/09 | Завершённый экран согласования на существующих API: видимая версия/hash, точный preview, отдельные approve/apply, чтение операции и receipt, различимые состояния apply/verify/rollback, безопасные повторы и ошибки. Unit/API/UI tests, tsc/lint/build; browser acceptance отдельно AR-9. |
| 2 | **AR-15 + оставшаяся AR-5 матрица**, REQ-WP §11.2–11.5; primary **AC-08** | Воспроизводимый устанавливаемый ZIP из актуальных исходников, release-manifest и проверка целостности/подписи с явно заданным trust bootstrap; tamper/traversal negative tests. Настоящие WP-проверки title/description/basic JSON-LD и missing/empty restore. Это тестовый пакет, не публикация релиза. |
| 3 | **AR-9**, REQ-WP/VERIFY/UI; primary **AC-08**, связь AC-09 | Изолированный browser-сценарий preview → approve → apply → public verified → rollback_verified с собранным плагином. Развить существующий собственный native stand; не открывать общий `up/e2e` обходом migration gate. Полный сценарий audit/sources/reports сюда не подменяется заранее созданной рекомендацией. |
| 4 | **AR-10**, REQ-SEMANTIC §9.5, DEC-04; primary **AC-06** | Одна `priority_v1=impact×confidence/effort`, отдельный urgency rank, stable sort, unknown без демонстрационного score, override с actor/reason. Использовать готовые входы текущего контракта; отсутствие входа сохранять, не ждать AL-10 и не выдумывать confidence. |
| 5 | **AR-14**, REQ-TILDA §13, DEC-02; primary **AC-10** | Supported read/manual, отсутствие `/updatepage` и ложного mock success в пользовательском пути, корректные ошибки/лимиты/webhook. Реальное подключение проверять только с разрешённой интеграцией; отсутствие ключей не заменять demo. |
| 6 | **AR-11 + своя часть AR-3/AR-16**, REQ-SECURITY §5.1, PLATFORM §4, RELIABILITY §15.4; primary **AC-12** | Проверки своей сети/service identities/scoped credentials, runtime grants, явные migration preflight, guards seed/cleanup, backup/restore текущего набора данных в собственный стенд, сохранность ledger/dedup и запрет автоматического повторного apply после restore. |
| 7 | **AR-12 + остаток AR-13**, REQ-WP §11.4, VERIFY §12; primary **AC-08** для managed blocks, **AC-09** для отдельной fault-задачи | H1/links через доказанный управляемый блок с exact rollback/manual fallback. Расширить недостающие native negative/concurrency/fault cases, не повторять уже закрытые тесты ради количества. CMS implementation независима; сквозные предложения links ждут AL-14. |
| 8 | **Своя часть AR-17/AR-18**, DEC-06, REQ-SECURITY/DoD; primary **AC-12** | Зафиксированная WP/theme/Yoast/RankMath матрица, аудит своего контура, актуальная трассировка требований и доказательств. Pilot/staging/release выполняются отдельным разрешённым шагом; общий финальный аудит зависит от готовности всего продукта. |

Порядок — очередь работы, а не искусственная зависимость. Если конкретный шаг требует внешнего доступа, решения о trust bootstrap или чужого контракта, зафиксировать этот блокер и продолжить независимую часть/следующую задачу. Не выдавать mock за native proof и не менять требования ради завершения пункта.

## Границы совместной работы

- AL-8/12: durable full crawl, browser egress, расписание и benchmark; AL-9/13: источники/provenance/OAuth; AL-10/14: прозрачность score и семантические расширения; AL-11/16: общая событийная модель и эксплуатационные механизмы; AL-15/17: reports и общий frontend остаются за Александрой.
- Memory access + защищённая refresh-cookie — отдельный остаток REQ-SECURITY §5.1 в её API/frontend. AL-6 не объявляется незавершённой задним числом: этот перенос не входил в объём её PR.
- Полное открытие общего `up/e2e`, миграции всех схем и readiness всех workers требуют устранения startup DDL и согласованной схемы/grants остальных сервисов. Наши собственные миграции/стенды можно разрабатывать и проверять сейчас.
- Выдача нового ZIP через общий API, общие события, новые поля upstream proposal и полный end-to-end от аудита до отчёта оформляются явными небольшими стыками с ревью владельца. Чужие каталоги не переписываются автоматически.

## Текущая задача: AR-8

Разрешённые файлы: `frontend/src/pages/HITL*`, `frontend/src/api/hitlAPI*`, `frontend/src/store/slices/hitlSlice.ts`, `frontend/src/types/hitl.ts`, `frontend/src/components/hitl/`, при необходимом совместимом дополнении — management HITL/execution DTO и `services/api_gateway/routes/protected/hitl_routes.py`, соответствующие тесты и docs. Схема рекомендации остаётся в Semantic, состояния операции — в Management; новый apply path не создаётся.

Начальный остаток по source review: версия/hash не показаны человеку; receipt отсутствует в карточке; нет отдельного просмотра authoritative operation; summary/manual statuses не раскрыты полностью. Нужны проверки, что обновление/просмотр только читает, поздний ответ не показывает чужую/старую операцию, receipt не превращается в verified, неизвестное состояние не разрешает запись. Менять формулу priority/impact в рамках AR-8 нельзя: это отдельная AR-10.

Результат первого инкремента AR-8: реализованы видимые версия/hash, read-only receipt/snapshots и защита от stale responses; frontend 250/250, tsc/lint/build, независимое source review. Подробная матрица и границы — [AR8_ACCEPTANCE.md](AR8_ACCEPTANCE.md). Следующий самостоятельный этап после передачи этого изменения — тестовый пакет актуального плагина и нативная field-матрица AR-15/AR-5; browser acceptance остаётся AR-9.

Каждый инкремент заканчивается применимыми проверками, документацией и отдельным PR. Слияние требует полного CI. Ни план, ни зелёный unit-набор не разрешают production deploy, миграцию общей БД, release tag или публикацию релиза.
