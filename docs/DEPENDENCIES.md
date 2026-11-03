# Зависимости и изолированные backend-проверки
## Контракт
Поддерживаемый интерпретатор — Python 3.11, генератор locks — ровно uv 0.11.3. Исходные `services/*/requirements.txt` и `adapters/tilda-adapter/requirements.txt` не подменяются ради успешных тестов.
`locks/<профиль>.txt` фиксирует полное дерево исходного manifest с SHA-256. `locks/<профиль>-test.txt` дополнительно содержит необходимые тестовые инструменты; runtime-lock является ограничением для test-lock. Это не означает, что исходные runtime manifests уже очищены от dev-зависимостей: например, audit и management сами объявляют pytest.
Профили:
- `management`: `services/management_service/requirements.txt`, 3 unit-файла.
- `client`: `services/client_api_gateway/requirements.txt`, 1 unit-файл.
- `api`: `services/api_gateway/requirements.txt`, 13 unit-файлов, включая shared projects/netpolicy и project integrations.
- `audit`: `services/audit_service/requirements.txt`, 8 unit-файлов.
- `semantic`: `services/semantic_service/requirements.txt`, 6 unit-файлов.
- `reporting`: `services/reporting_service/requirements.txt`, 2 unit-файла.
- `tilda`: `adapters/tilda-adapter/requirements.txt`, 1 unit-файл.
Все 36 существующих `tests/unit/test_*.py` распределены. Неизвестный unit-файл или изменение обнаруженного набора сервиса останавливает проверку до pytest; нельзя просто добавить тест и оставить его за пределами обязательного gate.
## Почему locks раздельны
Management требует httpx 0.26, client — 0.27, audit/reporting используют другие версии FastAPI, SQLAlchemy и pytest. Объединение manifests подменило бы реальные контейнерные зависимости.
`locks/pytest7.in` закрепляет pytest 7.4.4 / pytest-asyncio 0.23.8, `locks/pytest8.in` — pytest 8.3.3 / pytest-asyncio 0.24.0. Audit и reporting получают второй набор; остальные новые профили — первый. API test-lock добавляет respx 0.21.1, уже объявленный в audit manifest: без него целый файл public-gateway тестов превращался бы в module-level skip. Исходный httpx API при этом остаётся 0.26.0.
`locks/tooling.txt` — отдельное окружение платформенных инструментов: uv, mypy, ruff и тестовая инфраструктура. Не нужно добавлять его целиком в audit/reporting test-lock: его pytest 7 несовместим с исходным pytest 8.
## Воспроизводимые команды
Одинаковые команды работают в PowerShell и POSIX-shell из корня checkout:
- `python scripts/backend.py lock --check` — пересобрать locks во временном каталоге и сравнить. Зафиксированные версии используются как constraints, не заменяются текущими latest.
- `python scripts/backend.py lock` — явно обновить все locks. Это операция изменения исходников: diff и результаты тестов нужно проверить перед коммитом.
- `python scripts/backend.py check api` — создать disposable venv на текущем Python 3.11, установить hashed wheels и запустить профиль. Вместо `api` доступны все семь имён выше.
- `python scripts/backend.py test api` — выполнить профиль текущим интерпретатором; предполагается, что его зависимости уже установлены из соответствующего test-lock.
Генератор использует `--universal --python-version 3.11 --generate-hashes --no-build`, официальный PyPI и отключённую конфигурацию uv. Установка использует `--require-hashes --only-binary :all:`; если для целевой платформы нет wheel, gate должен упасть, а не запускать произвольный sdist build.
Universal-lock содержит platform markers, но не доказывает установку на каждом OS/CPU. CI проверяет Python 3.11 на Ubuntu 24.04 и Windows 2022. Native Windows-проверка не является Linux/image-проверкой.
## Изоляция тестов
Runner копирует в новый temporary snapshot только отслеживаемые Git Python-исходники из разрешённых каталогов и `pytest.ini`. Он не переносит `.env`, ключи, runtime-данные, ZIP/бинарные артефакты или untracked-файлы. Пути выхода из checkout, symlink/reparse-point и hardlink запрещены.
Приложения импортируются только дочерним pytest из snapshot. Рабочие application/tool environment variables не наследуются. Тестовые секреты генерируются на запуск, все service DSN указывают на sentinel `127.0.0.1:1`; prefixed AUDIT/SEMANTIC/REPORTING settings тоже задаются явно. HOME, USERPROFILE, APPDATA, LOCALAPPDATA и TEMP/TMP/TMPDIR дочернего pytest указывают внутрь его disposable workspace: тестовые SQLite-файлы и автоматические пользовательские кеши не должны попадать в рабочее окружение.
Автозагрузка pytest-плагинов выключена; явно включены pytest-asyncio и `scripts.pytest_required`. Skip при collection/runtime, xfail, xpass и пустой набор не считаются успешным обязательным gate. `-ra` сохраняет причины неполной проверки в выводе.
Сетевая политика устанавливается до начальных conftest:
- DNS, UDP sendto, доступный на POSIX `sendmsg`, внешние/private endpoints и чужие localhost-порты запрещены.
- Нативные входы psycopg2/psycopg connect и asyncpg connect/create_pool тоже запрещены, если соответствующий драйвер установлен: libpq может обойти Python socket hooks.
- Для существующих SSRF/trap/TLS тестов разрешены только собственные TCP listeners, созданные этим test process через `bind(("127.0.0.1", 0))` или `bind(("::1", 0))`. Проверяются AF_INET/AF_INET6, SOCK_STREAM и proto 0/IPPROTO_TCP у клиента и владельца listener; SCTP/другие протоколы не наследуют TCP-разрешение. Порт отслеживается по объекту socket и состоянию listen; wildcard/fixed-port bind запрещены.
- Для закрытого или ещё не слушающего собственного endpoint возвращается ECONNREFUSED без попытки контакта с портом. Для незарегистрированного endpoint разрешения нет.
- Стандартный socketpair для Windows asyncio разрешён узко внутри синхронного вызова socketpair.
Это защита от случайного I/O, **не security sandbox** для недоверенного Python/C-кода. Прямые нативные syscalls, другие процессы и конкурентная подмена состояния socket требуют OS/container isolation; такой гарантии эти hooks не дают.
IPv4 fixture lifecycle проверен реальными локальными socket. IPv6 authorization проверена с mock исходного connect syscall: native Windows host отказывает live IPv6 connect с WinError 10013. Это не заявляется как live IPv6 networking evidence.
## Проверенный результат и открытые gates
На Windows / Python 3.11.9 чистая установка с hashes прошла для всех семи профилей:
- management: 8 passed.
- client: 4 passed.
- api: 507 passed, без skip после добавления test-only respx.
- semantic: 2 passed.
- reporting: 4 passed.
- tilda: 6 passed.
- audit: общий `project test` после интеграции дал 65 passed, 2 failed; предыдущий отдельный запуск — 66 passed, 1 failed. Зелёного стабильного audit gate нет.
Audit остаётся красным, приложение и его тесты не подменены:
- `tests/unit/test_public_audit_core.py::test_request_budget_is_enforced` не подключает PSI fixture и пытается обратиться к `www.googleapis.com` через `technical_audit.py` / `psi_api.py`. Guard блокирует DNS до исходящего запроса. Нужна корректировка тестовой зависимости владельцем AL, не глобальная подмена PSI в платформенном runner.
- `tests/unit/test_public_audit_lifecycle.py::test_cancel_running_job_releases_resources` дважды получал SQLite `database is locked` при `cancel_public_audit` → `_finalize` → commit, но прошёл в финальном запуске: это незакрытый intermittent/flaky defect, не стабильный green. Финальный запуск также зафиксировал PytestUnhandledThreadExceptionWarning от aiosqlite с `Event loop is closed`. БД временная, рабочая PostgreSQL не используется; отказы нельзя скрывать retry/skip.
- Повторный общий прогон воспроизвёл тот же cancellation-тест иначе: статус `cancelled`, но resource cleanup не подтверждён (`cleaned == []`). Старые lock/thread сбои и новый assertion относятся к открытой lifecycle-проверке AL, а не к основанию ослабить тест.
Платформенные regressions и strict mypy/ruff изменяемых инструментов проверяются отдельно. Управляемая CI-зона — `scripts/backend.py`, `scripts/pytest_required.py`, интегрируемый `scripts/project.py`, `tests/platform` и отдельный WordPress runner после интеграции AR-5.
Legacy full-scripts quality не является зелёным: `scripts/cleanup_demo_data.py` и `scripts/seed_demo_data.py` импортируют прикладной стек и содержат E402. Попытка проверки всего `scripts/` в чистом tooling-окружении дала 255 mypy ошибок в 12 файлах и 8 ruff E402. Скрипты не переносились и исключения ради green не добавлялись; полный project lint остаётся самостоятельным блокирующим gate.
Зелёный unit-профиль также не доказывает container startup, Playwright browser installation, PostgreSQL migrations, consumer readiness, E2E topology, реальный WordPress/PHP lifecycle, производительность или deploy. Docker daemon на проверяемой машине недоступен; сборка/запуск сервисов и рабочие БД не использовались.
