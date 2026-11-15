# Проверки и матрица требований

Документ задаёт воспроизводимые проверки и ожидаемые свойства. Результаты конкретной ревизии хранятся в CI и PR. Наличие теста или успешный unit-набор не является формальной приёмкой всего AC.

## Среды и команды

Backend требует Python 3.11, точную версию uv и раздельные hash-locks из [DEPENDENCIES.md](DEPENDENCIES.md). Frontend использует Node/npm и package-lock, закреплённые CI. PHP executable передаётся явно; signed-package проверки требуют sodium. Профиль устанавливает зависимости в отдельный venv и проверяет tracked-source snapshot, без рабочего `.env`.

```text
python -B scripts/backend.py lock --check
python -B scripts/backend.py check management
python -B scripts/backend.py check client
python -B scripts/backend.py check api
python -B -m unittest discover -s tests/platform -v
python -B -m tests.wordpress.run --php <explicit-php-with-sodium>
```

Остальные backend-профили: `audit`, `semantic`, `reporting`, `tilda`. Frontend: `tsc --noEmit`, `npm run lint`, `npm test`, `npm run build` в `frontend/`. Полные обязательные команды, strict mypy/ruff и PostgreSQL-набор определены в `.github/workflows/ci.yml`; общий интерфейс — `scripts/project.py` / `scripts/project.ps1` / `Makefile`.

Tilda read-only/manual Beta: [контракт и ограничения](TILDA.md). Профиль `tilda` проверяет документированный read-протокол на fixtures и отсутствие upstream-записи. `tests/integration/test_tilda_postgres.py` запускается с `TEST_PG_ADMIN_URL` только на изолированном разрешённом PostgreSQL: миграции, durable ACK, dedup, квота, leases, scope и отдельное сравнение одобренной версии. Frontend проверяет восстановление по тому же check ID и отклонение ложных успешных ответов. Эти уровни не доказывают доставку реального callback, работоспособность аккаунта Tilda или автоматическую публикацию.

Настоящий CMS/HITL стенд запускается из management-test окружения:

```text
python -B -m tests.wordpress.native_contract --docker-context <context> --plugin-revision HEAD --with-management --with-apps
```

Он создаёт собственные помеченные временные Docker-ресурсы, использует закреплённые образы и проверяет принадлежность перед очисткой. Не запускается на общей dev/staging БД. Без `--plugin-revision` пакет имеет all-zero source_commit и является working-source fixture; с параметром используются Git blobs указанного commit. Signing/HMAC/receipt ключи генерируются для теста. Baseline обновления синтетический: он не доказывает миграцию всех исторических версий.

## Требование → проверка → ожидаемое свойство

| Требование / AC | Проверки | Ожидаемое свойство и уровень |
| --- | --- | --- |
| REQ-HITL §10, AC-07 | `test_management_approvals_postgres.py`, `test_management_execution_postgres.py`, `test_hitl_gateway.py` | Настоящий PostgreSQL: immutable версия/hash/target, actor и ownership, TTL и запрет неодобренной публикации. Proxy unit/stub отдельно проверяет передачу identity/references. |
| События HITL/outbox, AC-07/12 | `test_management_outbox_postgres.py`, `test_management_outbox_rabbitmq.py`, native HTTP stand | Atomic history/outbox, lease/stale ACK и повтор доставки без второго решения. PostgreSQL, настоящий RabbitMQ и HTTP/inbox проверяются отдельно; stub ACK не доказывает доставку брокером. |
| REQ-EVIDENCE §7, AC-07 | `test_audit_evidence_postgres.py`, `test_management_preparation_postgres.py` | Сохранённые evidence и версия, точные snapshots, один intent→frozen переход; отмена и поздние callbacks не разрешают запись. |
| REQ-CORE §3.2, REQ-PLATFORM, AC-03/12 | `test_management_migrations_postgres.py`, `test_management_canonical_postgres.py`, `test_runtime_foundation_postgres.py` | Empty/legacy БД, сохранность данных, отказ partial/drift/plaintext и небезопасного downgrade, readonly startup и no-DDL runtime roles. Равный домен не доказывает owner. |
| REQ-HITL §10.2, AC-08/09 | `test_management_recovery_postgres.py`, `test_management_preparation_postgres.py`, `test_management_recovery.py`, `test_client_recovery.py`, `test_client_preparation.py` | Durable leases, deadlines и общий budget; restart и конкурентный claim не создают новый apply. Неизвестный исход допускает только status/receipt той же операции. |
| REQ-VERIFY §12.3, AC-09 | `test_public_verifier.py`, `test_management_verification_postgres.py` | Anonymous policy-enforced fetch, missing/empty/duplicate predicates, durable retry и late-report fences; receipt не становится verified без публичных фактов. |
| REQ-VERIFY §12.4, AC-09 | Client rollback tests, PHP contracts, Management PostgreSQL verification suite | Явный rollback, текущие права, точный original.before, отказ drift/повторного rollback, самостоятельные receipt и verify. Scripted store не доказывает реальные SQL locks. |
| REQ-WP §11.2–11.4, AC-08 | `tests/wordpress/native_contract.py`, `native_authority.py`, `app_http.py` | Настоящие REST/mysqli/PostgreSQL/Redis/RabbitMQ и собранные сервисы: CAS, deadline после ожидания lock, same-op конкуренция, SIGKILL до write и после CMS commit, service-only recovery без повторной мутации. |
| REQ-WP §11.4, AC-08 | `tests/wordpress/field_matrix.py` | Post/page × missing/empty/Unicode × set/remove: persisted title/description/JSON-LD, signed status/receipt, anonymous HTML, точный rollback; post_title/content неизменны. Прямой CMS contract, без заявления о Semantic/browser workflow. |
| REQ-WP §11.5, AC-08 | `test_plugin_package.py`, `package-contract.php`, `tests/wordpress/native.py` | Настоящие cryptography/sodium: pinned key, подпись manifest и всех payload-файлов, canonical invalid signatures, Git provenance, budgets/traversal/ambiguous ZIP denials; real Plugin_Upgrader install/update, отказ исполнения tampered candidate и активации без trust. |
| REQ-HITL §10, REQ-REPORTS §14.4, AC-07/08/09 | HITL page, API-client, Redux и OperationDetails frontend tests | Видимые version/hash совпадают с решением; scoped GET и malformed identity не показывают чужие данные; late responses не восстанавливают старую операцию; snapshots инертны; просмотр не вызывает write. Unit/component, не browser E2E. |
| REQ-PLATFORM, AC-12 | `tests/platform`, `scripts/image_checks.py`, workflow image job | Locks/source inventory, guards и ownership cleanup; отдельно настоящие image builds и UID. UID probe переопределяет entrypoint и не доказывает lifespans/consumer readiness. |

## Браузерная проверка согласования

```text
python -u -B -m tests.e2e.hitl_browser --docker-context <context> --plugin-revision HEAD
```

Запуск требует management-test окружения и Docker Compose. Он использует контейнеры существующего native fixture, реальные Core/Management migrations, собранные API/Management/Client и frontend, подписанный WordPress ZIP и настоящий Chromium. `locks/browser.txt` задаёт отдельное окружение браузерного образа; версия Playwright согласована с audit через constraint.

Браузер входит через форму, проверяет видимые version/hash, отдельно одобряет и применяет предложение, читает receipt, ожидает независимый public verified и явно запрашивает rollback. Отдельно проверяются потеря настоящего ответа после apply, status-only обновление интерфейса, последовательные/конкурентные same-command retries и отказ rollback при manual drift. Матрица сравнивает UI с authoritative Management и настоящим WP ledger/полями.

До approval браузер отдельно задаёт входы priority_v1, сохраняет нулевой override, читает журнал и снимает override. Host сверяет revision и journal с PostgreSQL и подтверждает отсутствие approval и CMS-мутации от изменения очереди.

Конкурентный `503 storage_failure` не считается успешным apply: после завершения всех write-запросов тест требует подписанный status той же операции с исходными ID/hash. Новые apply/ID для такого исхода не создаются. Остальные ошибки проваливают проверку. Каждый итоговый receipt должен совпасть с исходным, managed snapshot — с ожидаемым after, ledger — содержать одну логическую операцию; число восстановленных через status результатов выводится отдельно от успешных ответов apply.

Проект, подтверждённые права, завершённый аудит/evidence и версия Semantic задаются persisted fixtures. Это проверка пути от готового предложения до отката; она не доказывает браузерное получение evidence, DomainAuthorization или генерацию предложения. Фикстура не открывает общий `up/e2e` и не запускает legacy mock/seed/cleanup scripts.

Все Compose-контейнеры используют уникальный owner/project и проверенные labels; очистка обращается только к этим ресурсам. Browser работает под UID10001/read-only в network namespace frontend с единственной внутренней сетью и настоящим loopback origin `http://127.0.0.1:8080`. WebCrypto использует штатную secure-context модель loopback origin. Context блокирует service workers и запросы вне этого origin. Конфигурация с синтетическими credentials находится в приватном temporary каталоге. Playwright traces, видео и полные call logs не публикуются: они могут содержать токены и введённый пароль.

## Проверка priority_v1

REQ-SEMANTIC §9.5 / DEC-04 / часть AC-06 проверяются `tests/unit/test_prioritizer.py`, `tests/integration/test_management_priority_postgres.py`, `tests/unit/test_hitl_gateway.py` и frontend tests `PriorityPanel`, `HITL`, `hitlAPI`. Unit проверяет контрольные значения, монотонность effort, границы, missing/zero и стабильные tie-break. Настоящий PostgreSQL проверяет формулу модели, SQL order до пагинации, scoped MANAGE/READ, атомарность журнала и rollback транзакции, гонку revision и сохранение override после audit/score/scheduler при stale identity map. Gateway проверяется на общей очереди двух проектов. Компонентные тесты проверяют authoritative order, unknown/zero, явный reason/revision, конфликт и инертный журнал. Это не доказывает остальные части AC-06 о score API/UI/CSV и источниках данных.

## Уровни доказательств и ограничения

Source review, unit/stub, native library, real database/CMS, image build/UID и staging/browser E2E — разные уровни. PHP memory/scripted SQL fixtures не доказывают InnoDB atomicity и crash recovery; такие свойства проверяет реальный стенд. HTTP 200, pending-list и CMS receipt не доказывают публичное verified. Обязательный skip/xfail, недоступная зависимость или стенд считаются незавершённой проверкой.

Полная WordPress/theme/Yoast/RankMath/CDN matrix, браузерный путь получения evidence/подтверждения прав/генерации предложения и реальная production release trust/key rotation требуют собственных разрешённых сред и доказательств. Общие `up/e2e` не открываются обходом migration gate. Технические ограничения CMS и trust bootstrap описаны в [WORDPRESS.md](WORDPRESS.md) и [PUBLISHING.md](../adapters/wordpress-plugin/PUBLISHING.md).
