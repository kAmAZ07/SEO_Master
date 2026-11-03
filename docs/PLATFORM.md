# Платформа: инкременты AR-1/AR-2

## Границы изменений
Документ описывает платформенные изменения Арсения. Задачи AL-4/AL-5 и остальные файлы Александры не изменяются. Новые отдельные контракты в `contracts/` не создаются.

Эти инкременты не закрывают AR-1/AR-2 целиком и не являются готовым P0-контуром. Первый защищает build context, ограничивает COPY в management-образе и вводит проверки PR. Следующий добавляет раздельные hash-locks и воспроизводимые unit-gates management/client gateway.

## Docker context
Корневой `.dockerignore` применяется к сборкам с `context: .`. Окружения, ключи, локальные БД, кэши, node_modules, логи и отчёты исключены. Management-образ копирует явный набор каталогов вместо всего checkout.

Нельзя создавать `Dockerfile.dockerignore` рядом с Dockerfile без отдельного пересмотра: Docker отдаёт ему приоритет над корневой политикой. Нельзя добавлять allow-исключения, возвращающие секреты в context. Файл с нестандартным именем, содержащий секрет, всё равно нужно хранить за пределами checkout: deny-list не заменяет secret storage и secret scanning.

Архив `adapters/wordpress-plugin/dist/*.zip` пока нужен существующему Dockerfile API Gateway, поэтому глобального запрета всех `dist/` и ZIP нет. Это не подтверждает подпись или безопасность содержимого архива; release-проверка пакета относится к AR-15.

## Локальные проверки без запуска сервисов
Проверки платформы требуют Python 3.11 и инструменты из `locks/tooling.txt`. В новом отдельном venv установите их с проверкой хешей (не синхронизируйте рабочее окружение):

    python -m pip install --require-hashes --only-binary=:all: -r locks/tooling.txt
    python -m unittest discover -s tests/platform -v
    python -m mypy --strict scripts/backend.py scripts/pytest_required.py tests/platform
    python -m ruff check scripts/backend.py scripts/pytest_required.py tests/platform

Platform-тесты не импортируют приложения, не открывают рабочий `.env`, не запускают Docker и не обращаются к БД. Регрессии pytest-policy запускают дочерний pytest с искусственными тестами, поэтому стандартной библиотеки для полного platform-набора теперь недостаточно. Статические Docker-тесты проверяют правила конфигурации, а не фактический tar context/runtime образа.

Frontend проверяется без изменения исходников:

    node frontend/node_modules/typescript/bin/tsc --noEmit -p frontend/tsconfig.json
    npm.cmd --prefix frontend run lint
    npm.cmd --prefix frontend test

Для Linux вместо `npm.cmd` используется `npm`. Локально команды требуют уже установленного окружения. В CI выполняется чистый `npm ci` по существующему lock; установленные локально пакеты не заменяют эту проверку.

## CI
`.github/workflows/ci.yml` запускается для PR, push в main и вручную. `Platform guards` проверяет locks, платформенные регрессии, strict mypy и Ruff новых инструментов. Backend matrix запускает management/client отдельно на Ubuntu 24.04 и Windows Server 2022. `Frontend quality` сохраняет чистый npm install, TypeScript, lint, unit и build. Токен имеет только `contents: read`, credentials не сохраняются, Actions закреплены по проверенным commit SHA. Workflow не использует deploy-секреты и ничего не публикует.

Если существующий frontend lint или тест не проходит, gate должен остаться красным. Правка в зоне Александры передаётся ей на согласование; отключать проверку или ставить `continue-on-error` нельзя.

CI ещё не включает остальные backend-сервисы, service builds, миграции, PHP runtime и реальный WordPress E2E. Успех новых unit-gates не означает готовность HITL/публикации и не заменяет негативные и конкурентные тесты следующих AR-задач.

## Locks и изолированные backend-тесты
Генератор — ровно uv 0.11.3, установленный через `locks/tooling.txt`, цель — Python 3.11. Источник runtime-зависимостей — исходный `requirements.txt` соответствующего сервиса. Management требует `httpx~=0.26.0`, client gateway — `httpx==0.27.0`: объединять их нельзя. Существующие manifests не изменяются; management manifest уже содержит dev/test-пакеты, поэтому первый faithful runtime-lock сохраняет их. Разделение runtime/dev — отдельная работа.

Наборы `locks/management.txt` и `locks/client.txt` закрепляют runtime; `*-test.in` добавляют инструменты, а `*-test.txt` разрешаются с runtime-lock в качестве constraint. `locks/tooling.txt` закрепляет сам uv, pytest, asyncio, mypy и Ruff с транзитивными зависимостями. Используются SHA-256, публичный PyPI, запрет сборки sdist. Runtime Dockerfile обоих сервисов устанавливают свой lock с `--require-hashes --only-binary=:all:`.

Locks универсальные с platform markers, но это не обещание поддержки всех ОС/архитектур: проверены только фактически выполненные native установки. Локальная проверка — Windows x86_64/Python 3.11.9; Linux/Windows CI matrix должна отдельно подтвердить свои результаты после публикации. Docker daemon локально недоступен, поэтому установка wheel не является проверкой образа. Закрепление старых версий не заменяет security audit и обновление уязвимых зависимостей.

Из корня checkout в PowerShell и POSIX одинаково, из отдельного Python 3.11 tooling-окружения:

    python scripts/backend.py lock --check
    python scripts/backend.py check management
    python scripts/backend.py check client

`check` создаёт одноразовый venv за пределами checkout, устанавливает test-lock с проверкой хешей и запускает полный профиль. Рабочий `.venv` не меняется. Затем venv/snapshot удаляются как собственные временные ресурсы; пользовательские данные/volumes и схема БД не затрагиваются. Нужны Git, uv точной версии и доступ к PyPI/кэшу.

Профиль management включает целиком `test_hitl_handler.py`, `test_orchestrator.py`, `test_prioritizer.py` (8 тестов); client — `test_client_gateway_rollback.py` (4). Новые тесты внутри файлов выполняются автоматически; новый unit-файл с прямой ссылкой `services.management_service`/`services.client_api_gateway` требует явного обновления профиля, иначе gate падает. Косвенное использование сервиса этим ограниченным inventory-detector не распознаётся: владелец нового теста обязан расширить профиль по смыслу.

Snapshot берёт **текущие рабочие байты отслеживаемых Git файлов**, не старый HEAD. Копируются только Python-исходники разрешённых каталогов и `pytest.ini`; `.env`, ключи, логи, git и venv не копируются. Новый исходник сначала добавляют в Git index; неизвестный import не маскируется. Абсолютные пути, `..`, выход после canonicalization, symlink и hardlink отвергаются. Это не sandbox для недоверенного Python и не защита от конкурентной подмены checkout другим процессом.

Дочернее окружение строится по OS-allowlist, без родительских DSN, ключей, `PYTEST_ADDOPTS`, `PYTHONPATH` и UV/PIP indexes. Тестовые ключи генерируются в памяти и не печатаются; DSN направлены на loopback port 1. Policy запрещает DNS/connect/sendto Python sockets до collection; Windows asyncio может создать только свой внутренний socketpair. Это guard случайного I/O, не OS-сетевая песочница: native extensions/произвольный subprocess им не изолируются. Рабочие сервисы запускать через этот runner нельзя.

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
- Исправление устаревших путей и конфигурации production Compose/root Dockerfile, согласование toolchain, service locks и запуск без root.
- Изолированный E2E-стек с уникальным именем и безопасной очисткой. Существующий `tests/e2e/run_extended_e2e.ps1` делает `down -v`; его нельзя запускать на общей dev-конфигурации как обычный тест.
- Доступный Docker daemon для build/runtime-проверок. Наличие Docker CLI не подтверждает доступность daemon.
- Проверенная связка WordPress/PHP/тема/SEO-плагин для DEC-06. Пустые значения нельзя заменять непроверенными обещаниями совместимости.

Автодеплой остаётся выключенным, пока `DEPLOY_ENABLED` не установлен владельцем. Старый deploy workflow не является безопасным production promotion и этим инкрементом не принимается.
