# Платформа: инкременты AR-1/AR-2

## Границы изменений
Документ описывает платформенные изменения Арсения. Задачи AL-4/AL-5 и остальные файлы Александры не изменяются. Новые отдельные контракты в `contracts/` не создаются.

Эти инкременты не закрывают AR-1/AR-2 целиком и не являются готовым P0-контуром. Первый защищает build context, ограничивает COPY в management-образе и вводит проверки PR. Следующий добавляет раздельные hash-locks и воспроизводимые unit-gates management/client gateway. Доработка топологии и общего runner описана ниже; до закрытия миграционных/runtime gates запуск остаётся намеренно недоступным.

## Docker context
Корневой `.dockerignore` применяется к сборкам с `context: .`. Окружения, ключи, локальные БД, кэши, node_modules, логи и отчёты исключены. Management-образ копирует явный набор каталогов вместо всего checkout.

Нельзя создавать `Dockerfile.dockerignore` рядом с Dockerfile без отдельного пересмотра: Docker отдаёт ему приоритет над корневой политикой. Нельзя добавлять allow-исключения, возвращающие секреты в context. Файл с нестандартным именем, содержащий секрет, всё равно нужно хранить за пределами checkout: deny-list не заменяет secret storage и secret scanning.

Архив `adapters/wordpress-plugin/dist/*.zip` пока нужен существующему Dockerfile API Gateway, поэтому глобального запрета всех `dist/` и ZIP нет. Это не подтверждает подпись или безопасность содержимого архива; release-проверка пакета относится к AR-15.

## Локальные проверки без запуска сервисов
Проверки платформы требуют Python 3.11 и инструменты из `locks/tooling.txt`. В новом отдельном venv установите их с проверкой хешей (не синхронизируйте рабочее окружение):

    python -m pip install --require-hashes --only-binary=:all: -r locks/tooling.txt
    python -m unittest discover -s tests/platform -v
    python -m mypy --strict --explicit-package-bases scripts/backend.py scripts/pytest_required.py scripts/project.py scripts/image_checks.py tests/platform tests/wordpress/run.py
    python -m ruff check scripts/backend.py scripts/pytest_required.py scripts/project.py scripts/image_checks.py tests/platform tests/wordpress/run.py

Platform-тесты не импортируют приложения, не открывают рабочий `.env`, не запускают Docker и не обращаются к БД. Регрессии pytest-policy запускают дочерний pytest с искусственными тестами, поэтому стандартной библиотеки для полного platform-набора теперь недостаточно. Статические Docker-тесты проверяют правила конфигурации, а не фактический tar context/runtime образа.

Frontend проверяется без изменения исходников:

    node frontend/node_modules/typescript/bin/tsc --noEmit -p frontend/tsconfig.json
    npm.cmd --prefix frontend run lint
    npm.cmd --prefix frontend test

Для Linux вместо `npm.cmd` используется `npm`. Локально команды требуют уже установленного окружения. В CI выполняется чистый `npm ci` по существующему lock; установленные локально пакеты не заменяют эту проверку.

## CI
`.github/workflows/ci.yml` запускается для PR, push в main и вручную. `Platform guards` проверяет locks, платформенные регрессии, strict mypy и Ruff новых инструментов. Backend matrix запускает все семь профилей отдельно на Ubuntu 24.04 и Windows Server 2022; `fail-fast: false` не скрывает результаты других профилей после одного отказа. `Frontend quality` сохраняет чистый npm install, TypeScript, lint, unit и build. Токен имеет только `contents: read`, credentials не сохраняются, Actions закреплены по проверенным commit SHA. Workflow не использует deploy-секреты и ничего не публикует.

Если существующий frontend lint или тест не проходит, gate должен остаться красным. Правка в зоне Александры передаётся ей на согласование; отключать проверку или ставить `continue-on-error` нельзя.

CI задаёт отдельные Linux image builds, но их фактический запуск ещё нужно подтвердить; миграции и реальный WordPress E2E не включены. Отдельный `WordPress isolated contracts (not E2E)` выполняет PHP syntax/contracts и общие Python/PHP vectors; это не запуск WordPress/MySQL. Успех новых unit-gates не означает готовность HITL/публикации и не заменяет негативные и конкурентные тесты следующих AR-задач.

### Linux-сборка и проверка UID без запуска стека
Job `Linux image builds and UID (not stack E2E)` на Ubuntu 24.04 создаёт одноразовую dev-конфигурацию через общий `bootstrap`, проверяет `config --quiet` и выполняет общий `build`. PostgreSQL, Redis, RabbitMQ, приложения и миграции при этом не запускаются. Нет deploy-секретов, push образов, host mounts или автоматического schema/seed. Установка пакетов и Chromium внутри build всё ещё требует сети.

Внутренний helper `python -m scripts.image_checks` проверяет восемь разных Dockerfile outputs (management worker/beat используют тот же Dockerfile). По документированному правилу Compose теги — `project-service:latest`; project берётся только из собственной закрытой dev-конфигурации после проверки локального Linux daemon. Он не принимает чужое имя стека или произвольные Docker-флаги.

Probe переопределяет application entrypoint: Python запускается с `-I -S -B` и импортирует только `os` для `geteuid()`, frontend выполняет BusyBox `id -u`. Ожидается фактический UID 10001 либо 101, без `--user`, который мог бы скрыть ошибку образа. Сеть отключена, filesystem read-only, capabilities удалены, healthcheck отключён, заданы CPU/memory/pids limits; ключи приложения, порты и host mounts не передаются.

После `docker create` сохраняется только проверенный новый 64-hex container ID; `start --attach` ограничен тайм-аутом. `finally` удаляет только этот созданный probe-контейнер и его новые anonymous image volumes. Named volumes не подключаются и не удаляются; shared cleanup/prune, Compose down и удаление чужих ресурсов отсутствуют. Ошибка создания/UID/timeout/cleanup даёт красный gate без вывода сырых Docker diagnostics.

Если `docker create` завершился тайм-аутом до получения подтверждённого ID, результат создания неизвестен: helper не угадывает контейнер по имени и не делает общий cleanup. Возможный orphan остаётся до уничтожения одноразовой hosted VM; поэтому этот CI-helper не предназначен для автоматической уборки на постоянном/shared daemon.

Локальные fake-subprocess регрессии проверяют команды, error paths и ownership cleanup, но **не доказывают сборку или запуск образа**. Недоступный локальный daemon остаётся блокером; только завершённый Linux CI run может подтвердить build/UID. Даже зелёный UID probe не проверяет application startup, readonly/tmpfs-поведение сервиса, readiness, browser egress, миграции или E2E. Источник правила tagging: `https://docs.docker.com/reference/cli/docker/compose/build/`.

## Locks и изолированные backend-тесты
Генератор — ровно uv 0.11.3, установленный через `locks/tooling.txt`, цель — Python 3.11. Источник runtime-зависимостей — исходный `requirements.txt` соответствующего сервиса. Management требует `httpx~=0.26.0`, client gateway — `httpx==0.27.0`: объединять их нельзя. Существующие manifests не изменяются; management manifest уже содержит dev/test-пакеты, поэтому первый faithful runtime-lock сохраняет их. Разделение runtime/dev — отдельная работа.

Первый инкремент добавил `locks/management.txt` и `locks/client.txt`; теперь runtime/test locks есть для всех семи профилей. `*-test.in` добавляют инструменты, а `*-test.txt` разрешаются с runtime-lock в качестве constraint. `locks/tooling.txt` закрепляет сам uv, pytest, asyncio, mypy и Ruff с транзитивными зависимостями. Используются SHA-256, публичный PyPI, запрет сборки sdist. Все сервисные Dockerfile устанавливают свой lock с `--require-hashes --only-binary=:all:`. Полный inventory, версии pytest, native результаты и незакрытые audit-дефекты приведены в `docs/DEPENDENCIES.md`.

Locks универсальные с platform markers, но это не обещание поддержки всех ОС/архитектур: проверены только фактически выполненные native установки. Локальная проверка — Windows x86_64/Python 3.11.9; Linux/Windows CI matrix должна отдельно подтвердить свои результаты после публикации. Docker daemon локально недоступен, поэтому установка wheel не является проверкой образа. Закрепление старых версий не заменяет security audit и обновление уязвимых зависимостей.

Из корня checkout в PowerShell и POSIX одинаково, из отдельного Python 3.11 tooling-окружения:

    python scripts/backend.py lock --check
    python scripts/backend.py check management
    python scripts/backend.py check client

`check` создаёт одноразовый venv за пределами checkout, устанавливает test-lock с проверкой хешей и запускает полный профиль. Рабочий `.venv` не меняется. Затем venv/snapshot удаляются как собственные временные ресурсы; пользовательские данные/volumes и схема БД не затрагиваются. Нужны Git, uv точной версии и доступ к PyPI/кэшу.

Профиль management включает целиком `test_hitl_handler.py`, `test_orchestrator.py`, `test_prioritizer.py` (8 тестов); client — `test_client_gateway_rollback.py` (4). Новые тесты внутри файлов выполняются автоматически; новый unit-файл с прямой ссылкой `services.management_service`/`services.client_api_gateway` требует явного обновления профиля, иначе gate падает. Косвенное использование сервиса этим ограниченным inventory-detector не распознаётся: владелец нового теста обязан расширить профиль по смыслу.

Snapshot берёт **текущие рабочие байты отслеживаемых Git файлов**, не старый HEAD. Копируются только Python-исходники разрешённых каталогов и `pytest.ini`; `.env`, ключи, логи, git и venv не копируются. Новый исходник сначала добавляют в Git index; неизвестный import не маскируется. Абсолютные пути, `..`, выход после canonicalization, symlink и hardlink отвергаются. Это не sandbox для недоверенного Python и не защита от конкурентной подмены checkout другим процессом.

Дочернее окружение строится по OS-allowlist, без родительских DSN, ключей, `PYTEST_ADDOPTS`, `PYTHONPATH` и UV/PIP indexes. Тестовые ключи генерируются в памяти и не печатаются; DSN направлены на loopback port 1. Policy до collection запрещает DNS, UDP/sendmsg и чужие endpoints; разрешает только собственные ephemeral TCP listeners и внутренний socketpair Windows asyncio. Закрытый listener не даёт права связаться с повторно занятым портом. Native DBAPI connect тоже блокируется; точные границы и регрессии описаны в `docs/DEPENDENCIES.md`. Это guard случайного I/O, не OS-сетевая песочница: произвольные native syscalls/subprocess им не изолируются. Рабочие сервисы запускать через этот runner нельзя.

Пропуск/xfail/xpass, пустая коллекция, отсутствующий пакет/plugin, ошибка установки и любое падение теста возвращают ненулевой код. Произвольные pytest-фильтры не принимаются. Прямой внутренний `test <profile>` предназначен для уже изолированного окружения; обычная локальная/CI-команда — `check <profile>`.

Изменение зависимостей: править исходный manifest/`*.in`, выполнить `python scripts/backend.py lock`, проверить весь lock-diff и оба профиля. `lock --check` пересчитывает набор с существующими версиями как constraints и сравнивает результат, не подбирая незапрошенные новые версии. Это проверка соответствия входам, не утверждение «все пакеты самые новые». Обновление версий производится осознанным отдельным diff, без dirty `pip freeze`.
## Владельцы и независимое ревью
В `.github/CODEOWNERS` назначены Арсений (`@kAmAZ07`) и Александра (`@sshpv`) по декомпозиции AL/AR. По умолчанию неперечисленные файлы относятся к платформенной зоне Арсения. Новые прикладные модули и тесты Александры нужно добавлять в правила в том же PR.

Общие правила каталогов Александры стоят раньше исключений: HITL-фронтенд, его типы и тесты, HITL-модули Gateway, сервисные Dockerfile, frontend lock и `database/migrations/env.py` принадлежат Арсению. `frontend/src/App.tsx`, фактический `frontend/src/store/store.ts`, `database/models.py` и `README.md` остаются за Александрой. `frontend/src/components/hitl/` пока не создан: правило закрепляет согласованную зону, а не подтверждает наличие компонентов. Для будущей management-линии миграций явное правило добавляется вместе с её каталогом в AR-3.

Тесты `tests/platform/test_codeowners.py` проверяют эти границы и порядок совпадений. Для прозрачности файл использует только `*`, точные пути от корня и каталоги от корня — тестовый resolver не претендует на полную реализацию синтаксиса GitHub.

GitHub учитывает CODEOWNERS из базовой ветки PR; локальное добавление файла ещё не включает автоматическое назначение. Оба участника должны иметь write-доступ к репозиторию; доступы здесь не проверялись и не изменялись.

CODEOWNERS обозначает ответственность, но не заменяет независимое ревью. При единственном владельце зоны нельзя бездумно включать обязательное Code Owner approval: автор не может одобрить собственный PR, а второй участник не является code owner этой зоны. Режим защиты main и способ обязательного неавторского review нужно согласовать отдельно; этим изменением защита ветки не настраивается.

## Что требуется отдельно
- Согласованная настройка защиты main с required checks и review. Добавление CI-файла само её не включает.
- Подтверждение Linux-сборок обновлённых Dockerfile, согласование оставшихся locks и реальный запуск без root/read-only. Исправленные исходные файлы сами по себе этого не доказывают.
- Изолированный E2E-стек с уникальным именем и безопасной очисткой. Существующий `tests/e2e/run_extended_e2e.ps1` делает `down -v`; его нельзя запускать на общей dev-конфигурации как обычный тест.
- Доступный Docker daemon для build/runtime-проверок. Наличие Docker CLI не подтверждает доступность daemon.
- Проверенная связка WordPress/PHP/тема/SEO-плагин для DEC-06. Пустые значения нельзя заменять непроверенными обещаниями совместимости.

Автодеплой остаётся выключенным, пока `DEPLOY_ENABLED` не установлен владельцем. Старый deploy workflow не является безопасным production promotion и этим инкрементом не принимается.

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

`test` выполняет platform-регрессии, все семь backend-профилей, чистые frontend unit/build и обязательный standalone PHP/vector runner. Пока соответствующий профиль/runner отсутствует или падает, команда красная, а не «частично зелёная». Standalone PHP не считается реальным WordPress E2E. `lint` проверяет весь `scripts/` и платформенные тесты, frontend TypeScript/ESLint и PHP syntax; legacy demo-скрипты могут оставлять полный gate красным. CI-проверки новых platform-инструментов имеют явно обозначенную область и не означают строгую типизацию всего старого приложения.

Повторная чистая frontend-проверка с изолированной npm-конфигурацией: установлено 457 пакетов, 6 unit-тестов и production build прошли, `tsc --noEmit` прошёл. ESLint остаётся красным: 5 ошибок/3 предупреждения в зоне AL; исходники не изменялись. TypeScript 5.9.3 находится вне объявленного диапазона установленного typescript-eslint 6 (`<5.4`): успешная сборка не закрывает совместимость toolchain. Эти зависимости и AL-дефекты нельзя скрывать отключением правил.

## Канонический Compose и образы
`docker-compose.yml` теперь единственный источник топологии; dev/prod-файлы — overlays, не самостоятельные стеки. Оба включают API Gateway, audit/semantic/reporting, client gateway, Tilda, management web/worker/beat и frontend. Убраны отсутствующие nginx/monitoring/provisioning binds и несуществующие legacy workers; это не реализация мониторинга и старых очередей. Реальные management-очереди сохранены через `services.management_service.tasks.celery_app:celery_app`.

Корневой `Dockerfile` — совместимый alias management-образа, а не несуществующий монолит. Другие сервисы собираются только своим Dockerfile. Python-образы используют hash-lock, Python 3.11 и явного non-root пользователя; исходники root-owned, writable только ограниченные tmpfs для временных файлов/логов/отчётов. Убраны компиляторы, curl и `chmod 777`, не нужные при binary wheels. Frontend собирается отдельным `docker/frontend.Dockerfile` с `npm ci`, работает через unprivileged nginx и проксирует `/api/` без изменения пути. Обязательная проверка фактического поведения read-only/tmpfs ещё не выполнена.

Все внешние base/runtime images закреплены multiarch SHA-256 indexes. Digests сверены с сырыми manifest-байтами public registries без daemon/pull; это проверяет подлинность конкретной ссылки, не запуск, отсутствие CVE или актуальность upstream. Node — версия уже выбранного CI 24.14.1; Python — 3.11-slim-bookworm. PostgreSQL остаётся major 15, без скрытого major upgrade; старые тома не подключаются и не изменяются.

Playwright Jammy 1.49.1 использует системный Python 3.10, поэтому audit собирается на том же Python 3.11-bookworm; locked Playwright 1.49.1 сам устанавливает соответствующий Chromium в `/ms-playwright`. Установка browser assets/apt-dependencies требует сети и ещё не проверена Linux-сборкой: pin не делает этот шаг hermetic. Browser egress/изоляция AL также не закрыты.

Redis закреплён на 7.4; конфигурация с паролем создаётся внутри контейнера с umask 077, health использует `REDISCLI_AUTH`, не `redis-cli -a`. RabbitMQ 3.13 закреплён для минимизации несовместимых изменений, но **community support закончилась 30.09.2024**: это явный release blocker и требует отдельного совместимого обновления/проверки, а не заявления о production support. Источники: `https://www.rabbitmq.com/release-information`, `https://redis.io/docs/latest/operate/oss_and_stack/install/version-mgmt/`, `https://github.com/microsoft/playwright-python/blob/v1.49.1/utils/docker/Dockerfile.jammy`.

Нет fixed container/project/volume/network names, `external`-ресурсов, checkout/dotenv binds, SQL init mounts и host-портов PostgreSQL/Redis/RabbitMQ/внутренних HTTP-сервисов. Единственный ingress — loopback `127.0.0.1:8080` frontend. Dev не предоставляет hot reload через весь checkout. Ограничения памяти/CPU/pids — заданные лимиты, не измеренная ёмкость. Конкурентным dev-стекам пока нужен отдельный ingress-port сценарий; случайный автоматический поиск порта не реализован.

Явные `AUDIT_`, `SEMANTIC_`, `REPORTING_` DB/Redis/RabbitMQ настройки исправляют несовпадение env-prefix. CORS не использует `*` с credentials; auto-approval выключен. Proxy forwarding по умолчанию не доверяется: публичный лимитер видит frontend как одного клиента, то есть лимит для локального стенда общий. Нельзя расширять trust до всех private сетей ради обхода этого ограничения; production ingress/trusted identity — отдельный gate.

### Почему запуск остаётся закрыт
Audit startup выполняет schema/create_all/partition DDL и cleanup/recovery; management — create_all; client — create_all, ALTER/UPDATE и `DROP COLUMN secret`. Эти действия нельзя запускать на рабочей БД как часть обычного `up`. В новой топологии runtime DSN заранее отделён от владельца БД, но роль `seo_runtime`, grants и схема ещё **не создаются**. Без AR-3 такой стек заведомо не готов к запуску; фиктивный migration container, флаг «согласовано» или один `depends_on` это не исправляют.

До открытия `up/e2e` нужны версионированные миграции/роли, устранённый startup DDL, тесты empty/legacy DB на отдельном разрешённом стенде, безопасный browser egress и полноценная readiness. Management health использует `/health/ready`, worker — адресованный inspect ping. Beat status проверяет доступность worker, но ещё не доказывает свежесть scheduler heartbeat; `/health` остальных сервисов также не доказывает живой consumer. Эти ограничения остаются открытыми.

AL-4 уже находится в базе ветки, но его `legacy_project_id_map` нельзя использовать как подтверждение владельца: `_map_management_projects` сопоставляет единственный canonical project по host без доказательства owner/workspace. Задача исправления остаётся за AL; AR-3 не переносит разрешения или историю по такому `matched`. Alembic `env.py` дополнительно требует отдельной доработки: сейчас inspection-команды могут выполнять DDL/commit, metadata неполно, а фильтр autogenerate не ограничен владельцем таблиц. Исследование не запускало эти команды против БД.

Bootstrap production-ключей намеренно отсутствует; `--mode prod` не является production deploy-процессом. Для настоящего staging/production необходимы внешний secret provider, TLS/ingress, миграционное разрешение, backup/restore, immutable artifact promotion и согласованная проверка. Никакая сборка или проверка Compose не меняет рабочую БД и не включает старый автодеплой.
