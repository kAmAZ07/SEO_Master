# Подписанный пакет WordPress: AR-15

Пакет 0.3.0-dev не является RC. Релиз, production key, tag и deploy требуют отдельного поручения. Проверяемая подпись подтверждает происхождение и целостность; она не доказывает совместимость со всеми темами/SEO-плагинами или актуальность версии.

## Сборка

Использовать Python 3.11 из чистого окружения `locks/management-test.txt` (либо `locks/api-test.txt`). Зависимости уже закреплены, отдельная установка crypto не требуется. Из корня репозитория:

```text
python -B -m scripts.plugin_package build --revision <commit> --output <new.zip> --signing-key-env <RELEASE_KEY_ENV_NAME>
```

В указанной переменной защищённый release-процесс предоставляет base64 32-байтового Ed25519 seed. Не вводить приватный ключ аргументом, не писать его в `.env`, исходники, ZIP или логи. Команда не генерирует production key, не публикует результат, не перезаписывает существующий файл и не удаляет каталоги. PowerShell wrapper требует явный путь к этому Python, Output и SigningKeyEnv. Версия берётся из согласованных plugin header, constant и readme; отдельного override нет.

Источник — только blobs указанного Git commit. Рабочие изменения, mtime, CRLF checkout, ignored/untracked файлы в релиз не попадают. Все 15 payload-файлов (PHP, JS, CSS и readme) перечислены с размером и SHA-256 в каноническом `release-manifest.json`; manifest подписан Ed25519 с контекстом `SEO-MASTER-PLUGIN-RELEASE-V1`. `release-signature.json` содержит только signature/key ID, без доверенного ключа. Два metadata-файла исключены из self-hash. ZIP детерминированный, без сжатия, с одним корнем `seo-master-connector/`, фиксированными timestamp/mode и точным allowlist. Одинаковые commit и key дают одинаковые байты.

## Внешнее доверие и установка

1. Оператор получает публичный ключ по независимому доверенному каналу, вне ZIP/download endpoint, и закрепляет JSON `{SHA256(raw_public_key): base64(raw_public_key)}`. Допускается 1–4 ключа для контролируемой ротации. Приватный release key не является HMAC/receipt ключом CMS.
2. До распаковки или первого выполнения PHP проверить скачанный ZIP внешним инструментом из доверенной ревизии:

```text
python -B -m scripts.plugin_package verify --package <downloaded.zip> --trust-file <trusted-public-keys.json> --sha256 <operator-approved-archive-sha256>
```

3. Только проверенные байты передать штатному WordPress installer. Файл не заменять между проверкой и загрузкой; при повторном скачивании проверять заново. Manifest проверяет подпись и каждый файл, exact ZIP layout, budgets, traversal/links, отсутствующие и лишние файлы. Публичный ключ из самого архива не принимается.
4. До активации закрепить тот же public-key JSON вне каталога плагина: константа `SEO_MASTER_RELEASE_KEYS_JSON` в защищённом `wp-config.php` (приоритет), либо серверное окружение PHP. Требуется расширение sodium. Отсутствие доверия, metadata или sodium, изменение/добавление файла блокируют работу и активацию. `SEO_MASTER_PLUGIN_SIGNATURE` больше не применяется.
5. Обновление проходит такую же внешнюю проверку. Runtime hook дополнительно проверяет распакованное дерево до замены плагина. При намеренном обходе внешней проверки внутренний hook откажет после штатного pre_install: WordPress уже может деактивировать старый плагин, хотя его файлы сохранены. Реактивировать проверенное старое дерево. После ручного обновления проверить/выполнить штатную реактивацию, затем capabilities и подписанный challenge. Без этого результат установки не означает работающий CMS transport.

Runtime verifier внутри пакета — дополнительная защита от изменения других файлов. Он не доказывает безопасность сервера, на котором атакующий заменил одновременно loader/verifier/trust config. Поэтому bootstrap вне пакета обязателен. Не выдавать SHA-256 за асимметричную подпись. Подпись старого пакета также валидна: разрешённый digest и ротацию/отзыв ключей выбирает оператор, автоматического `latest` по mtime нет.

## Выдача через API

Три обязательные настройки API: `WORDPRESS_PLUGIN_PACKAGE_PATH`, `WORDPRESS_PLUGIN_PACKAGE_SHA256`, `WORDPRESS_PLUGIN_RELEASE_KEYS_JSON`. Путь находится внутри контейнера; подключить выбранный ZIP read-only отдельным mount/override. По умолчанию download отвечает 503, старый dist не используется. Endpoint проверяет внешний ключ и digest и отдаёт точный проверенный byte snapshot с no-store/nosniff, без повторного открытия пути. JSON с публичными ключами не является секретом; release seed в API не передаётся.

Общий compose `up` остаётся закрыт существующим migration gate. Настройка выдачи не разрешает обход этого gate. Изменение общего plugin route требует ревью Александры перед merge.

## Приёмка

`tests/unit/test_plugin_package.py`: настоящие Ed25519 и ZIP parser, все payload tamper, trust/hash/manifest, traversal/ambiguous archive, Git blob provenance, API exact bytes и отказ конфигурации. `tests/wordpress/run.py`: native sodium и source fixtures, без WordPress. `tests/wordpress/native_contract.py`: настоящий Plugin_Upgrader install/update и CMS/HITL HTTP на изолированном WordPress 6.8.3/PHP8.3/MariaDB10.11. Полная WP/theme/Yoast/RankMath матрица — отдельный AR-17, H1/links — AR-12; v1 mutation остаётся 426. Подробные результаты и ограничения — `docs/AR15_ACCEPTANCE.md`.
