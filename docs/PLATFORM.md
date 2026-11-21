# Платформа: сборка, конфигурация и запуск

## Назначение
Руководство описывает Docker build context, hash-locks, изолированные проверки, migration gates и локальный интерфейс запуска. Матрица требований и уровни доказательств — [TESTING.md](TESTING.md).

## Docker context
Корневой `.dockerignore` применяется к сборкам с `context: .`. Окружения, ключи, локальные БД, кэши, node_modules, логи и отчёты исключены. Management-образ копирует явный набор каталогов вместо всего checkout.

Нельзя создавать `Dockerfile.dockerignore` рядом с Dockerfile без отдельного пересмотра: Docker отдаёт ему приоритет над корневой политикой. Нельзя добавлять allow-исключения, возвращающие секреты в context. Файл с нестандартным именем, содержащий секрет, всё равно нужно хранить за пределами checkout: deny-list не заменяет secret storage и secret scanning.

API-образ содержит `adapters/wordpress-plugin/dist/README.md`; release ZIP исключён из обычного build context и подключается явно read-only. Настройки выдачи и внешний trust bootstrap — [PUBLISHING.md](../adapters/wordpress-plugin/PUBLISHING.md).

## Локальные проверки без запуска сервисов
Общий runner канонизирует найденный PHP executable через `Path.resolve(strict=True)` до смены каталога дочернего процесса. Это относится и к symlink `python → python3.11` в Linux test fixture: ожидается разрешённый путь PHP-аргумента, а не буквальный `sys.executable`. Обязательная portable-регрессия дополнительно использует существующий путь с `..`, поэтому проверяет эту границу без прав на создание symlink в Windows; production canonicalization не ослабляется.
Проверки платформы требуют Python 3.11 и инструменты из `locks/tooling.txt`. В новом отдельном venv установите их с проверкой хешей (не синхронизируйте рабочее окружение):

    python -m pip install --require-hashes --only-binary=:all: -r locks/tooling.txt
    python -m unittest discover -s tests/platform -v
    python -m mypy --strict --explicit-package-bases scripts/backend.py scripts/pytest_required.py scripts/project.py scripts/image_checks.py tests/platform tests/wordpress/run.py
    python -m ruff check scripts/backend.py scripts/pytest_required.py scripts/project.py scripts/image_checks.py tests/platform tests/wordpress/run.py

Platform-тесты не импортируют приложения, не открывают рабочий `.env`, не запускают Docker и не обращаются к БД. Регрессии pytest-policy запускают дочерний pytest с искусственными тестами, поэтому стандартной библиотеки для полного platform-набора теперь недостаточно. Статические Docker-тесты проверяют правила конфигурации, а не фактический tar context/runtime образа.

`test_workflow_yaml.py` обязательно разбирает все `.github/workflows/*.yml` через `yaml.safe_load` из закреплённого PyYAML 6.0.3; type stubs включены в tooling-lock и наследующие его management/client test-locks. Runtime requirements/locks и прежние package/hash blocks не меняются. Проверяются наличие jobs/steps и строковый тип команд `run`, отрицательный случай с двоеточием-пробелом и отказ от Python object tags. Две команды pip переведены в YAML block scalar без изменения shell-команды: прежнее plain `--only-binary=:all: -r ...` делало workflow синтаксически недействительным ещё до создания jobs. Parser-тест не заменяет GitHub Actions schema validation, фактический CI run или security review deploy workflow.

Frontend проверяется без изменения исходников:

    node frontend/node_modules/typescript/bin/tsc --noEmit -p frontend/tsconfig.json
    npm.cmd --prefix frontend run lint
    npm.cmd --prefix frontend test

Для Linux вместо `npm.cmd` используется `npm`. Локально команды требуют уже установленного окружения. В CI выполняется чистый `npm ci` по существующему lock; установленные локально пакеты не заменяют эту проверку.

## CI
`.github/workflows/ci.yml` запускается для PR, push в main и вручную. `Platform guards` проверяет locks, платформенные регрессии, strict mypy и Ruff новых инструментов. Backend matrix запускает все семь профилей отдельно на Ubuntu 24.04 и Windows Server 2022; `fail-fast: false` не скрывает результаты других профилей после одного отказа. `Frontend quality` сохраняет чистый npm install, TypeScript, lint, unit и build. Токен имеет только `contents: read`, credentials не сохраняются, Actions закреплены по проверенным commit SHA. Workflow не использует deploy-секреты и ничего не публикует.

Если существующий frontend lint или тест не проходит, gate должен остаться красным. Правка в зоне Александры передаётся ей на согласование; отключать проверку или ставить `continue-on-error` нельзя.

### Linux-сборка и проверка UID без запуска стека
Job `Linux image builds and UID (not stack E2E)` на Ubuntu 24.04 создаёт одноразовую dev-конфигурацию через общий `bootstrap`, проверяет `config --quiet` и выполняет общий `build`. PostgreSQL, Redis, RabbitMQ, приложения и миграции при этом не запускаются. Нет deploy-секретов, push образов, host mounts или автоматического schema/seed. Установка пакетов и Chromium внутри build всё ещё требует сети.

Внутренний helper `python -m scripts.image_checks` проверяет восемь разных Dockerfile outputs (management worker/beat используют тот же Dockerfile). По документированному правилу Compose теги — `project-service:latest`; project берётся только из собственной закрытой dev-конфигурации после проверки локального Linux daemon. Он не принимает чужое имя стека или произвольные Docker-флаги.

Probe переопределяет application entrypoint: Python запускается с `-I -S -B` и импортирует только `os` для `geteuid()`, frontend выполняет BusyBox `id -u`. Ожидается фактический UID 10001 либо 101, без `--user`, который мог бы скрыть ошибку образа. Сеть отключена, filesystem read-only, capabilities удалены, healthcheck отключён, заданы CPU/memory/pids limits; ключи приложения, порты и host mounts не передаются.

После `docker create` сохраняется только проверенный новый 64-hex container ID; `start --attach` ограничен тайм-аутом. `finally` удаляет только этот созданный probe-контейнер и его новые anonymous image volumes. Named volumes не подключаются и не удаляются; shared cleanup/prune, Compose down и удаление чужих ресурсов отсутствуют. Ошибка создания/UID/timeout/cleanup даёт красный gate без вывода сырых Docker diagnostics.

Если `docker create` завершился тайм-аутом до получения подтверждённого ID, результат создания неизвестен: helper не угадывает контейнер по имени и не делает общий cleanup. Возможный orphan остаётся до уничтожения одноразовой hosted VM; поэтому этот CI-helper не предназначен для автоматической уборки на постоянном/shared daemon.

Fake-subprocess регрессии проверяют команды, error paths и ownership cleanup. Настоящие image build/UID probes выполняет отдельный CI job; они не заменяют startup, browser, schema или E2E проверки.

## Locks и изолированные backend-тесты
Генератор — ровно uv 0.11.3, установленный через `locks/tooling.txt`, цель — Python 3.11. Источник runtime-зависимостей — исходный `requirements.txt` соответствующего сервиса. Management требует `httpx~=0.26.0`, client gateway — `httpx==0.27.0`: объединять их нельзя. Существующие manifests не изменяются; management manifest уже содержит dev/test-пакеты, поэтому первый faithful runtime-lock сохраняет их. Разделение runtime/dev — отдельная работа.

Runtime/test locks определены для семи backend-профилей. `*-test.in` добавляют инструменты, а `*-test.txt` разрешаются с runtime-lock как constraint. `locks/tooling.txt` закрепляет uv, pytest, asyncio, mypy и Ruff с транзитивными зависимостями. Используются SHA-256, публичный PyPI и запрет сборки sdist. Сервисные Dockerfile устанавливают свой lock с `--require-hashes --only-binary=:all:`. Inventory и команды — [DEPENDENCIES.md](DEPENDENCIES.md).

Locks универсальные с platform markers. Поддержка конкретной ОС/архитектуры требует native установки и профильных проверок. Установка wheel не заменяет image build/runtime probe; закрепление версии не заменяет security audit.

Из корня checkout в PowerShell и POSIX одинаково, из отдельного Python 3.11 tooling-окружения:

    python scripts/backend.py lock --check
    python scripts/backend.py check management
    python scripts/backend.py check client

`check` создаёт одноразовый venv за пределами checkout, устанавливает test-lock с проверкой хешей и запускает полный профиль. Рабочий `.venv` не меняется. Затем venv/snapshot удаляются как собственные временные ресурсы; пользовательские данные/volumes и схема БД не затрагиваются. Нужны Git, uv точной версии и доступ к PyPI/кэшу.

Профиль management включает целиком `test_hitl_handler.py`, `test_orchestrator.py`, `test_prioritizer.py`, `test_management_model_imports.py`, `test_management_scores.py`, `test_management_proposals.py` и `test_management_approvals.py`; client — `test_client_gateway_rollback.py` и `test_client_model_imports.py`. Профиль audit включает `test_audit_page_evidence.py`, API — `test_hitl_gateway.py`. Стыки nullable-баллов, evidence AL-5 и строгого решения описаны в `docs/MANAGEMENT.md`. Новые тесты внутри файлов выполняются автоматически; новый unit-файл с прямой ссылкой `services.management_service`/`services.client_api_gateway` требует явного обновления профиля, иначе gate падает. Косвенное использование сервиса этим ограниченным inventory-detector не распознаётся: владелец нового теста обязан расширить профиль по смыслу.

Snapshot берёт **текущие рабочие байты отслеживаемых Git файлов**, не старый HEAD. Копируются только Python-исходники разрешённых каталогов и `pytest.ini`; `.env`, ключи, логи, git и venv не копируются. Новый исходник сначала добавляют в Git index; неизвестный import не маскируется. Абсолютные пути, `..`, выход после canonicalization, symlink и hardlink отвергаются. Это не sandbox для недоверенного Python и не защита от конкурентной подмены checkout другим процессом.

Дочернее окружение строится по OS-allowlist, без родительских DSN, ключей, `PYTEST_ADDOPTS`, `PYTHONPATH` и UV/PIP indexes. Тестовые ключи генерируются в памяти и не печатаются; DSN направлены на loopback port 1. Policy до collection запрещает DNS, UDP/sendmsg и чужие endpoints; разрешает только собственные ephemeral TCP listeners и внутренний socketpair Windows asyncio. Закрытый listener не даёт права связаться с повторно занятым портом. Native DBAPI connect тоже блокируется; точные границы и регрессии описаны в `docs/DEPENDENCIES.md`. Это guard случайного I/O, не OS-сетевая песочница: произвольные native syscalls/subprocess им не изолируются. Рабочие сервисы запускать через этот runner нельзя.

Пропуск/xfail/xpass, пустая коллекция, отсутствующий пакет/plugin, ошибка установки и любое падение теста возвращают ненулевой код. Произвольные pytest-фильтры не принимаются. Прямой внутренний `test <profile>` предназначен для уже изолированного окружения; обычная локальная/CI-команда — `check <profile>`.

Изменение зависимостей: править исходный manifest/`*.in`, выполнить `python scripts/backend.py lock`, проверить весь lock-diff и оба профиля. `lock --check` пересчитывает набор с существующими версиями как constraints и сравнивает результат, не подбирая незапрошенные новые версии. Это проверка соответствия входам, не утверждение «все пакеты самые новые». Обновление версий производится осознанным отдельным diff, без dirty `pip freeze`.
## Alembic: безопасный source-адаптер AR-3
`database/migrations/env.py` больше не импортирует приложение, `config.database_config`, secret provider или модели, не вызывает helper DDL и не делает собственный commit. URL берётся только из явно настроенного `sqlalchemy.url`; альтернативно вызывающий код передаёт уже открытую connection через `Config.attributes["connection"]`. Скрытого fallback на `DATABASE_URL` и рабочий `.env` нет. Собственные connection/engine закрываются и освобождаются при успехе и ошибках; borrowed connection не закрывается и не коммитится адаптером. Транзакцией revisions управляет Alembic.

Правила `.gitattributes` задают text/EOL для отдельных legacy-файлов; Git хранит канонический LF-текст. Общая перенормализация checkout не требуется.

Пустой tracked URL в `database/alembic.ini` намеренно приводит к `MIGRATION_DSN_MISSING` до создания engine. Ошибки чтения/interpolation INI и разбора offline URL дают только `MIGRATION_DSN_INVALID` с подавленным исходным traceback; literal `%` в INI требует стандартного escaping `%%`. Ошибки создания собственного engine/connection дают `MIGRATION_CONNECTION_FAILED`. Ошибки самих revisions/transaction не маскируются как неверный DSN. Эти границы не делают безопасным логирование произвольных ошибок внутри legacy revisions.

Чистая stdlib policy `scripts/migration_policy.py` фиксирует qualified identities `(schema, table)` для core 33, management 6 и client 2 таблиц; `None` нормализуется в `public`, но `audit_schema.projects` и `public.projects` остаются разными владельцами. Фильтры исключают чужие schemas/tables до reflection и сверяют table parent у columns/indexes/constraints. Отсутствующие, дублирующие и чужие metadata identities отклоняются. Это декларация владения, **не** полный загруженный ORM registry и не разрешение DROP.

Пока полного чистого registry нет, `check`, `revision --autogenerate` и генерация через `revision_environment=True` блокируются как `MIGRATION_AUTOGENERATE_BLOCKED` до engine/reflection. Проверяются и CLI, и programmatic Alembic API. `target_metadata=None` не используется как основание удалить отсутствующие модели. Autogeneration gate не разрешает применение revisions и не заменяет проверку их DDL и startup-поведения.

Обязательные stdlib/fake регрессии входят в platform-набор. Отдельный CI job устанавливает существующий `locks/management-test.txt` с проверкой хешей: Alembic 1.13.3, SQLAlchemy и mypy находятся в одном изолированном job-окружении. Выполняются:

    python -B -m tests.platform.test_migration_context --native-alembic
    python -B -m mypy --strict --explicit-package-bases scripts/migration_policy.py scripts/migration_context.py database/migrations/env.py tests/platform/test_migration_policy.py tests/platform/test_migration_context.py

Native probe использует настоящий Alembic и временный пустой versions-каталог, запрещает application imports, dotenv, сеть/native DBAPI и проверяет отсутствие engine/version-DDL/revision callback в отказных сценариях. Это не скрытый skipped test: отсутствие закреплённых зависимостей завершает команду ошибкой. При отдельном tooling Python для mypy нужно добавить `--python-executable <python-из-management-test-окружения>`; strict только со stdlib-фейками без реальных vendor types не считается проверкой `env.py`. Регрессия workflow требует mandatory native/type шаги без `if`/`continue-on-error`.

Полный registry и schema parity, отдельные migration/runtime roles, empty/legacy PostgreSQL, backup/restore и отсутствие startup DDL проверяются до открытия `up/e2e`. Source adapter сам по себе не даёт разрешения применять миграции к общей БД.
### Чистые ORM-определения management/client
Канонические определения находятся в `services/management_service/models.py` и `services/client_api_gateway/models.py`. Эти модули загружают SQLAlchemy metadata без импорта `db` packages, настроек, `.env`, engine или AL-моделей. `db/models.py` содержат совместимые aliases, включая management `HITLStatus`: это те же объекты классов, enums, mapper, Table и `Base.metadata`.

Management metadata содержит 6 таблиц, Client metadata — 2. Mappings и порядок колонок заданы соответствующими чистыми моделями и миграциями; startup hooks и DDL при импорте запрещены.

**Обычный импорт по старому `.db.models` пути всё ещё исполняет runtime-bound parent `db/__init__.py`.** Для чистого чтения metadata используется только новый канонический путь. Старые runtime callers не переписывались. Изолированное исполнение доверенного файла alias через `runpy` в тесте проверяет только object identity reexports; оно не используется для загрузки ORM-определений и не является доказательством чистоты старого package import.

Native model/import тесты обязательны в соответствующих backend-профилях и Windows/Linux CI matrix. Management проверяется с SQLAlchemy 2.0.55/Pydantic 2.5.3, Client — со своим runtime-lock.

Проверяются реальные 6+2 таблицы и полные наборы колонок, ownership policy, identity aliases/mapper/Table, multiplicity aliases, единственный module-level registry и точный состав его mapped classes. Зафиксированы String lengths, все FK targets/ondelete, полные enum members/values, UUID/JSONB и dict defaults; дополнительно проверяются ORM relationships и компиляция PostgreSQL table/index expressions. Это ограниченный regression-контракт, а не исчерпывающая проверка каждого свойства legacy mappings. Создание engine, DDL, настройки/секреты, `.env` и случайная сеть блокируются. Компиляция SQL ничего не применяет к БД; это не schema parity, PostgreSQL-тест или запуск сервиса. Core registry остаётся неполным; Alembic adapter и его fail-closed generation не изменены. Startup/runtime DDL, роли, empty/legacy DB и `up/e2e` остаются открытыми.

## Владельцы и независимое ревью
В `.github/CODEOWNERS` назначены Арсений (`@kAmAZ07`) и Александра (`@sshpv`) по декомпозиции AL/AR. По умолчанию неперечисленные файлы относятся к платформенной зоне Арсения. Новые прикладные модули и тесты Александры нужно добавлять в правила в том же PR.

Общие правила каталогов Александры стоят раньше исключений: HITL-фронтенд, его типы и тесты, HITL-модули Gateway, сервисные Dockerfile, frontend lock и `database/migrations/env.py` принадлежат Арсению. `frontend/src/App.tsx`, фактический `frontend/src/store/store.ts`, `database/models.py` и `README.md` остаются за Александрой. Для `frontend/src/components/hitl/` действует отдельное правило владельца. Каталог `services/management_service/migrations/` закреплён за Арсением вместе с ledger revision; runner/version table/grants Management-линии остаются AR-3.

Тесты `tests/platform/test_codeowners.py` проверяют эти границы и порядок совпадений. Для прозрачности файл использует только `*`, точные пути от корня и каталоги от корня — тестовый resolver не претендует на полную реализацию синтаксиса GitHub.

GitHub учитывает CODEOWNERS из базовой ветки PR; локальное добавление файла ещё не включает автоматическое назначение. Оба участника должны иметь write-доступ к репозиторию; доступы здесь не проверялись и не изменялись.

CODEOWNERS обозначает ответственность, но не заменяет независимое ревью. При единственном владельце зоны нельзя бездумно включать обязательное Code Owner approval: автор не может одобрить собственный PR, а второй участник не является code owner этой зоны. Режим защиты main и способ обязательного неавторского review нужно согласовать отдельно; этим изменением защита ветки не настраивается.

## Условия запуска и публикации
- Согласованная настройка защиты main с required checks и review. Добавление CI-файла само её не включает.
- Для публикуемой ревизии требуются image build/UID и отдельные application startup/read-only проверки.
- Изолированный E2E-стек с уникальным именем и безопасной очисткой. Существующий `tests/e2e/run_extended_e2e.ps1` делает `down -v`; его нельзя запускать на общей dev-конфигурации как обычный тест.
- Доступный Docker daemon для build/runtime-проверок. Наличие Docker CLI не подтверждает доступность daemon.
- Проверенная связка WordPress/PHP/тема/SEO-плагин для DEC-06. Пустые значения нельзя заменять непроверенными обещаниями совместимости.

Автодеплой выключен, пока `DEPLOY_ENABLED` не установлен владельцем. Production promotion требует собственного проверенного процесса.

## Единый интерфейс Make/PowerShell
`Makefile` и `scripts/project.ps1` вызывают один `scripts/project.py`. Bash, GNU Make и Unix toolchain не обязательны для Windows. Общие требования: Python 3.11, инструменты из hash-locked tooling-окружения; backend-профили дополнительно требуют uv 0.11.3. Frontend устанавливается заново во временный tracked-source snapshot и требует ровно Node.js 24.14.1 с комплектным npm, как в CI/build image; рабочий `node_modules` не используется. PHP передаётся явно через `--php`, `-Php` или Make-переменную `PHP`.

Из корня checkout, после установки tooling в отдельное окружение:

    python scripts/project.py bootstrap
    python scripts/project.py config
    python scripts/project.py test --php /path/to/php
    python scripts/project.py lint --php /path/to/php
    python scripts/project.py build
    python scripts/project.py down

Эквивалент в PowerShell:

    .\scripts\project.ps1 bootstrap
    .\scripts\project.ps1 config
    .\scripts\project.ps1 test -Php C:\path\to\php.exe
    .\scripts\project.ps1 lint -Php C:\path\to\php.exe
    .\scripts\project.ps1 build
    .\scripts\project.ps1 down

Make предоставляет `bootstrap`, `config`, `up`, `test`, `e2e`, `lint`, `build`, `down`; параметры — `PYTHON`, `PHP`, `MODE`. Команды `up` и `e2e` пока возвращают код 2 с `MIGRATION_GATE_BLOCKED` **до любого Docker-вызова**. Это незакрытый gate, не успешный no-op и не выполненный E2E.

`bootstrap` генерирует только локальные dev-ключи через `secrets`, не запускает сервисы, не устанавливает пакеты и не делает DDL/seed. Конфигурация сохраняется в игнорируемом `.seo-local/dev/environment.env`; каталог исключён и из Docker context. На POSIX права каталога/файла — 0700/0600; на Windows выставляется текущий владелец, удаляются унаследованные и чужие явные ACE, проверяется защищённый DACL с единственным owner SID/FullControl. И каталог, и файл проверяются до чтения существующих ключей. Повторный bootstrap сохраняет project name и ключи, неизвестные/дублирующие параметры, ссылки и неверный формат отвергаются. Это локальный файл с открытыми ключами, защищённый ОС, не production secret storage. Не копируйте его в чаты, Git, логи или артефакты.

Рабочие `.env`, `.env.production`, DSN, `COMPOSE_*`, `DOCKER_*`, `NODE_OPTIONS`, индексы pip/uv и npm overrides из родительской сессии не наследуются. Frontend получает отдельные временные HOME/USERPROFILE/APPDATA, cache и TEMP/TMP/TMPDIR, разные пустые user/global npm configs и явный публичный registry. Node/npm/PHP пути канонизируются до смены дочернего cwd. Compose получает явные `--env-file`, `--project-directory`, два `--file` и уникальный `--project-name`. `config` использует только `--quiet`: отрендеренный конфиг и ошибки Docker с возможной интерполяцией секретов не печатаются. `build` и `down` дополнительно требуют строго локальный npipe/unix Docker context и доступный Linux daemon; удалённый npipe authority отвергается до daemon-вызова. Произвольные Compose-флаги и команды не принимаются.

`down` останавливает только project из собственной dev-конфигурации и **не** передаёт `-v`, `--volumes`, `--remove-orphans` или `--rmi`. Удаление томов/данных в интерфейсе отсутствует. CLI не является границей доступа к Docker: прямой `docker compose up` обходит runner и до закрытия gates запрещён эксплуатационной процедурой.

Прежние `scripts/seed_demo_data.py` и `scripts/cleanup_demo_data.py` возвращают код 2 (`DEMO_COMMAND_DISABLED`) до открытия БД при любом окружении. Для демонстрации сценариев используйте отдельные фикстуры из [TESTING.md](TESTING.md), которые создают и очищают только собственные временные ресурсы. Отключение команд сохраняет ранее созданные данные. Разбор и удаление данных старой демонстрации требуют отдельного подтверждения принадлежности проекта и сохранности журнала операций.

`test` выполняет platform-регрессии, все семь backend-профилей, чистые frontend unit/build и обязательный standalone PHP/vector runner. Пока соответствующий профиль/runner отсутствует или падает, команда красная, а не «частично зелёная». Standalone PHP не считается реальным WordPress E2E. `lint` проверяет весь `scripts/` и платформенные тесты, frontend TypeScript/ESLint и PHP syntax; legacy demo-скрипты могут оставлять полный gate красным. CI-проверки новых platform-инструментов имеют явно обозначенную область и не означают строгую типизацию всего старого приложения.

## Канонический Compose и образы
`docker-compose.yml` — единственный источник топологии; dev/prod-файлы — overlays, не самостоятельные стеки. Они включают API Gateway, audit/semantic/reporting, client gateway, Tilda, management web/worker/beat и frontend. Management-очереди задаются `services.management_service.tasks.celery_app:celery_app`.

Сеть `ingress` соединяет только frontend и API Gateway. Frontend публикует порт на `127.0.0.1:8080`. Остальные сервисы не публикуют порты на хост. Nginx возвращает 404 для префиксов `/internal`, `/api/internal`, `/api/client` и `/changes/pending`, включая точные пути и подмаршруты.

API Gateway и Client Gateway получают отдельный `CLIENT_EXECUTION_API_KEY` для `/internal/executions`. Дополнить старую локальную конфигурацию можно повторным `scripts/project.py bootstrap`: команда добавит недостающий ключ и сохранит существующие значения. При настройке внешней среды оба сервиса должны получить один новый случайный ключ. Остальным сервисам этот ключ не передаётся. Направление Client → Management использует прежний отдельный executor credential. [Контракт исполнения](EXECUTION.md).

Оператор задаёт ключи прежнего Client HMAC-протокола по проекту через `CLIENT_API_HMAC_KEYS_JSON` в конфигурации сервиса. Client синхронизирует метаданные ключей при проверке подписанного запроса. Старые HTTP-маршруты чтения и ротации `/api/client/keys/{project_id}` и `/api/client/keys/{project_id}/rotate` на Client возвращают 410 без обращения к БД. Общий сервисный ключ больше не позволяет читать или менять эти записи. Ключи WordPress v2 настраиваются отдельно по [контракту WordPress](WORDPRESS.md).

Корневой `Dockerfile` — alias management-образа. Другие сервисы собираются своими Dockerfile. Python-образы используют hash-lock, Python 3.11 и non-root runtime; исходники root-owned, writable только ограниченные tmpfs для временных файлов/логов/отчётов. Frontend собирается `docker/frontend.Dockerfile` с `npm ci`, работает через unprivileged nginx и проксирует `/api/` без изменения пути. Read-only/tmpfs поведение требует runtime-проверки приложения.

Внешние base/runtime images закреплены SHA-256 digests. Digest проверяет идентичность артефакта, но не его запуск, отсутствие CVE или актуальность upstream. Node — 24.14.1, Python — 3.11-slim-bookworm, PostgreSQL — major 15. Major upgrade и перенос существующих томов требуют отдельной процедуры.

Audit использует Python 3.11-bookworm и locked Playwright 1.49.1 с соответствующим Chromium в `/ms-playwright`. Установка browser assets/apt-dependencies требует сети. Image build/UID не заменяет проверку Chromium rendering и browser egress.

Redis закреплён на 7.4; конфигурация с паролем создаётся внутри контейнера с umask 077, health использует `REDISCLI_AUTH`, не `redis-cli -a`. RabbitMQ 3.13 закреплён для минимизации несовместимых изменений, но **community support закончилась 30.09.2024**: это явный release blocker и требует отдельного совместимого обновления/проверки, а не заявления о production support. Источники: `https://www.rabbitmq.com/release-information`, `https://redis.io/docs/latest/operate/oss_and_stack/install/version-mgmt/`, `https://github.com/microsoft/playwright-python/blob/v1.49.1/utils/docker/Dockerfile.jammy`.

Нет fixed container/project/volume/network names, `external`-ресурсов, checkout/dotenv binds, SQL init mounts и host-портов PostgreSQL/Redis/RabbitMQ/внутренних HTTP-сервисов. Единственный ingress — loopback `127.0.0.1:8080` frontend. Dev не предоставляет hot reload через весь checkout. Ограничения памяти/CPU/pids — заданные лимиты, не измеренная ёмкость. Конкурентным dev-стекам пока нужен отдельный ingress-port сценарий; случайный автоматический поиск порта не реализован.

DB/Redis/RabbitMQ настройки сервисов используют явные префиксы `AUDIT_`, `SEMANTIC_`, `REPORTING_`. CORS запрещает `*` с credentials; auto-approval выключен. Proxy forwarding по умолчанию не доверяется: публичный лимитер видит frontend как одного клиента, поэтому локальный лимит общий. Production ingress требует конкретного trusted proxy/identity; доверять всем private сетям ради обхода лимита запрещено.

### Почему запуск остаётся закрыт

Execution authority использует отдельные no-DDL runtime roles, read-only FS и UID10001. Client/Audit/Semantic и event resilience проверяют мигрированную схему при startup; legacy Client plaintext schema отклоняется. Heads, adoption и Core producer barrier описаны в [EXECUTION.md](EXECUTION.md). Bootstrap добавляет отсутствующий executor credential без ротации остальных dev-ключей; WordPress bindings/request+receipt env keys передаются явно.

До общего запуска необходимы полные runtime grants, worker/scheduler readiness, browser egress и legacy migration/restore. Роль `seo_runtime` и grants автоматически не создаются. Native test stand создаёт собственные роли и мигрирует только свою временную БД.
До открытия `up/e2e` нужны версионированные миграции/роли, устранённый startup DDL, тесты empty/legacy DB на отдельном разрешённом стенде, безопасный browser egress и полноценная readiness. Management health использует `/health/ready`, worker — адресованный inspect ping. Beat status проверяет доступность worker, но ещё не доказывает свежесть scheduler heartbeat; `/health` остальных сервисов также не доказывает живой consumer. Эти ограничения остаются открытыми.

`legacy_project_id_map` и совпадение host не доказывают owner/workspace старой строки. При переносе нельзя выдавать права по одному `matched`; нужны проверенная карта владельцев и сохранность истории. Alembic source adapter блокирует autogeneration при неполной metadata и не заменяет проверку самих revisions.

Bootstrap production-ключей намеренно отсутствует; `--mode prod` не является production deploy-процессом. Для настоящего staging/production необходимы внешний secret provider, TLS/ingress, миграционное разрешение, backup/restore, immutable artifact promotion и согласованная проверка. Никакая сборка или проверка Compose не меняет рабочую БД и не включает старый автодеплой.
