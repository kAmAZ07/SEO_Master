# WordPress: ограниченное ядро AR-5, протокол v2
Статус: исходники `0.3.0-dev`, не release candidate и не завершение всего AR-5/AC-08. Существующий `adapters/wordpress-plugin/dist` не пересобирался и **не содержит v2**. Старые ZIP нельзя представлять как новый протокол. Подпись полного release manifest, проверка устанавливаемого пакета и упаковка относятся к отдельному AR-15; старый optional SHA-256 главного файла не является такой подписью.
## Доказательства и ограничения
Нативные standalone PHP-тесты проверяют протокол, сервис с инъецированным memory store, сам `SM_V2_WPDB_Store` со строгим scripted SQL adapter и границы WordPress через явно обозначенные stubs. Store tests выполняют настоящую store-логику/SQL templates/transaction guards, но не нативный mysqli transport. Это **не** доказательство запуска WordPress, реальных InnoDB-транзакций, гонок, crash recovery, работы темы, Yoast или RankMath.
Минимальные API-требования: PHP 8.0+, WordPress 6.7+. Верифицированных сочетаний WordPress/PHP/MySQL/тем пока нет; `Tested up to` удалён. `capabilities.runtime_validation` прямо сообщает `pending_real_wordpress_innodb_matrix`.
Реальный стенд не запускался: Docker daemon недоступен. Общая БД, рабочий `.env`, live activation/DDL и старый destructive E2E не используются. Ограничение нельзя обходить подменой WordPress mock-сервером.
## Доверительная граница и pairing
Администратор с `manage_options` задаёт `seo_master_project_id` и `seo_master_site_id` — canonical lowercase UUID (версии 1–5). Форма использует Settings API с nonce; настройки не публикуются через Settings REST. Неверное значение очищает привязку, а не включает wildcard.
В защищённой конфигурации окружения / `wp-config.php` нужны:
- `SEO_MASTER_KEY_ID`: ASCII `[A-Za-z0-9_-]`, 1–64 символа.
- `SEO_MASTER_HMAC_SECRET`: случайный секрет, 64–128 lowercase hex-символов.
- `SEO_MASTER_RECEIPT_SECRET`: отдельный независимый случайный секрет того же формата. Совпадение ключей, однообразные демонстрационные значения и неполная привязка отклоняются.
- `SEO_MASTER_TRUSTED_GATEWAY=1`: явное разрешение apply только для подключённого доверенного Gateway. По умолчанию apply закрыт.
Ни UI, ни REST не сохраняют секреты в `wp_options`, не показывают их и не пишут в логи. Прежний alias `WORDPRESS_HMAC_SECRET` не принимается.
HMAC подтверждает интеграционного principal, а не пользователя WordPress и не факт человеческого одобрения. `approval_ref` — обязательная привязанная ссылка, **не авторитетное доказательство HITL**. До будущего AR-4 Gateway обязан проверить пользователя/роль, владельца project/site, актуальные ownership, proposal/version/hash, действительность/срок/отзыв approval и разрешённый target. Не включать флаг trusted gateway для старого v1 dispatcher или неавторизованного caller. Подписанный authorization envelope AR-4 пока не реализован.
Смена project/site или ротация ключей требует остановки публикации и reconciliation pending-операций. Ключ receipt нужно сохранять доступным для проверки исторических квитанций; keyring/бесшовная ротация здесь не реализованы. БД содержит только request hashes, receipts и nonce hashes, не signing keys.
## Маршруты
Все canonical routes начинаются с `/seo-master/v2`, без `/wp-json`, URL host, query, trailing slash, percent-encoding или повторных `/`. Для обычного HTTP это `/wp-json` плюс canonical route. WordPress `rest_route` используется как транспортный selector при plain permalinks, но не входит в подписываемый route; прочие query-параметры запрещены.
- `GET /capabilities` — публичное discovery: версии, наличие привязки, storage readiness, ограничения, допустимые targets/fields/operations. Не доказывает владение ключом; project/site не раскрывает. Нужен ingress rate limit, в том числе для этого маршрута.
- `POST /challenge` — подписанный challenge-response.
- `POST /snapshot` — аутентифицированный консистентный snapshot.
- `POST /dry-run` — проверка полного apply payload без изменения бизнес-полей и ledger операций.
- `POST /apply` — один атомарный управляемый набор.
- `GET /operations/{operation_id}` — durable status/receipt после потери ответа; те же HMAC-проверки. Неизвестный/чужой scope даёт 404.
Все POST требуют `Content-Type: application/json`. На границе лимит тела 16384 байта, глубины JSON 16; duplicate keys (включая escaped aliases), неверный UTF-8, literal/escaped NUL, лишние поля и неверные типы отвергаются. JSON object/list kind сохраняется до проверки составной схемы: changes обязан быть настоящим JSON array, каждая операция — JSON object; numeric-key objects (включая reordered keys) не преобразуются в lists. Имена объектов протокола фиксированы и не могут быть числовыми. REST не использует пользовательский id из тела как идентичность.
Все v1 PATCH (`meta`, `schema`, `interlinks`) и их прежние внутренние mutation helpers возвращают `upgrade_required`, HTTP 426. Legacy HMAC v1 также закрыт. Это намеренный отказ от небезопасной совместимости: Client Gateway должен получить отдельную v2-интеграцию; в этом изменении он не редактировался.
## HMAC v2: точные байты
Обязательные заголовки: `X-SEO-Project-ID`, `X-SEO-Site-ID`, `X-SEO-Key-ID`, `X-SEO-Timestamp`, `X-SEO-Nonce`, `X-SEO-Operation-ID`, `X-SEO-Signature`.
Timestamp — ровно 10 десятичных цифр unix seconds, окно ±300 секунд. Nonce — 32–64 ASCII символа `[A-Za-z0-9_-]`, криптографически случайный и новый для каждой транспортной попытки. Operation ID — UUID для apply/dry-run/status; для snapshot/challenge строго `-`. Status UUID в route и header должны совпадать.
Сообщение — следующие десять строк через LF, **без завершающего LF**:
1. `SEO-MASTER-HMAC-V2`
2. uppercase HTTP method
3. exact canonical route
4. lowercase hex SHA-256 **исходных байтов тела**, не повторно сериализованного JSON
5. timestamp header
6. nonce header
7. key-id header
8. project-id header
9. site-id header
10. operation-id header
Signature — lowercase hex `HMAC-SHA256(message, secret)`. Hex secret используется как ASCII-строка, не hex-decoded bytes. Сравнение через `hash_equals`. Любое изменение method/route/body/timestamp/nonce/key/project/site/operation требует новой подписи. Для GET тело пустое, digest — SHA-256 пустой строки.
Общие `tests/vectors/wordpress/v2.json` различают literal/escaped Unicode, composed/combining символы, пробелы, кавычки, слеши и U+2028/U+2029. Ключей в vectors нет: runner генерирует их в памяти и передаёт PHP только в transient test environment.
Nonce claim — отдельный autocommit INSERT с уникальным SHA-256 `(key_id + LF + nonce)` до бизнес-транзакции. Неверная подпись не создаёт claim. Успешная аутентификация расходует nonce даже при последующем schema/CAS conflict/rollback. Повтор transport nonce — `replay`, 409. Логический retry использует **тот же operation id и те же байты body**, но новый nonce/timestamp/signature.
## Snapshot, canonical digest и proposal
Snapshot request содержит только `project_id`, `site_id`, `entity_id`; `entity_id` — JSON integer 1…2147483647, не строка/URL/bool/float.
Snapshot содержит `entity_id`, `post_type`, `post_status` и `fields` с фиксированными `title`, `description`, `jsonld`. Каждое поле — либо `{"state":"missing"}`, либо `{"state":"value","value":"точные байты UTF-8 строки"}`. Существующая пустая строка отличается от отсутствия. Before/after хранятся в receipt целиком, а не восстанавливаются из предположений.
Storage mapping фиксирован: title → `_seo_master_meta_title`; description → `_seo_master_meta_description`; jsonld → `_seo_master_schema_jsonld`. Другие метаполя не редактируются.
Canonical JSON здесь — узкий контракт схемы, не универсальная реализация RFC 8785: ASCII object keys сортируются лексикографически, порядок arrays сохраняется, без пробелов, UTF-8/слеши/U+2028/U+2029 не экранируются, JSON quote/backslash/control escaping сохраняется. Дробных чисел нет. Digest — lowercase hex SHA-256 canonical JSON. Пустые JSON objects в этих структурах не используются. Не применять Unicode-нормализацию.
Apply/dry-run требуют **все и только** поля:
- `project_id`, `site_id`, integer `entity_id`;
- UUID `operation_id`, `task_id`, `proposal_version`, `approval_ref`;
- hex SHA-256 `proposal_hash`, `before_digest`, `expected_after_digest`;
- `changes`: непустой array, максимум три операции, не больше одной на поле.
`proposal_hash` = SHA-256 от `SEO-MASTER-PROPOSAL-V2` + LF + canonical JSON команды **без** `operation_id`, `approval_ref`, `proposal_hash`. Таким образом task/version/target/scope/changes/before/expected-after входят в hash. Gateway обязан отдельно связать `approval_ref` именно с этим hash; reference вместе с operation id входит в HMAC, request hash и receipt.
Request hash для идемпотентности — SHA-256 исходных body bytes. Даже другое оформление JSON с тем же смыслом под использованным operation id даёт `idempotency_conflict`, 409.
## Разрешённые изменения
Автоматический target — только опубликованные (`publish`) непарольные `post` и `page` на привязанном WP-сайте. Draft/private/revision/attachment/custom post types/парольные записи дают одинаковый отказ `target_not_found`. URL-to-post resolution отсутствует.
- `{"op":"set","field":"title","value":"..."}`: только managed SEO title, 1–512 UTF-8 bytes.
- `{"op":"set","field":"description","value":"..."}`: plain text, 1–2000 UTF-8 bytes.
- `{"op":"set","field":"jsonld","value":"JSON string"}`: максимум 8192 bytes; object с обязательными `@context:"https://schema.org"`, `@type` из `WebPage`/`Article`/`Organization`, `name`, необязательным `description`. Других ключей, graphs, URL, HTML или произвольной вложенности нет. Это basic JSON-LD, не обещание rich-result eligibility.
- `{"op":"remove","field":"..."}`: строго без value; after становится missing.
Unknown op/field, missing/null/empty/whitespace-only set values, лишние ключи, повторы поля, управляющие символы/NUL, `<`/`>`, XSS и oversize отклоняются **для всего набора до первой бизнес-записи**. PHP serialized-looking values также запрещены: прямое хранение таких строк нельзя передавать WP `maybe_unserialize`. Значения не «исправляются» санитайзером после одобрения.
`post_title`, H1, content/HTML, internal links не изменяются. Прежний `the_title` hook отключён. Наличие старых `_seo_master_h1`/`_seo_master_interlinks` не превращает их в реализованную capability.
Title/description выводятся через core/Yoast/RankMath filters; без SEO-плагина description выводится в `wp_head` с `esc_attr`. JSON-LD выводится с `JSON_HEX_TAG|AMP|APOS|QUOT`, не может закрыть script element. Существующие значения повторно проверяются перед выводом.
С Yoast/RankMath JSON-LD **manual/unsupported**: v2 отклоняет такую запись (`unsupported_field`, 422), graph filters оставляют сторонние узлы без изменений. Эта консервативная граница сохраняется до реальной merge/conflict compatibility matrix. Произвольная тема/плагин может создавать второй description/schema: публичный verifier обязателен, универсальное отсутствие дублей не заявляется.
## Транзакционная модель и fail-closed
Реализация в `includes/v2-store.php` требует обычный single-site-table mysqli/mysqlnd backend с trusted `$wpdb->prefix/posts/postmeta`, без `db.php` drop-in/sharding. Multisite/network activation и нестандартные database backends не сертифицированы.
Проверяются:
- InnoDB и utf8mb4 для posts, postmeta, operations и nonces;
- подходящие single-column primary keys, индекс post_id, схема ledger/nonce;
- autocommit=1, strict SQL mode, `innodb_flush_log_at_trx_commit=1`, отсутствие DB triggers при подтверждённом прямом TRIGGER grant текущему account (иначе metadata могла бы скрыть triggers и storage отклоняется);
- отсутствие уже открытой чужой транзакции; несовместимые настройки не меняются автоматически.
Role-only grants и неоднозначные/необычные SQL account names в этом ограниченном backend не поддерживаются. Права автоматически не расширяются; их согласование для изолированного стенда — отдельный gate.
**Отдельный deployment blocker:** WordPress 6.7 `wpdb::set_sql_mode()` по умолчанию удаляет `STRICT_TRANS_TABLES`/`STRICT_ALL_TABLES` как несовместимые режимы. Одного server-global strict mode недостаточно: проверяется эффективный `@@SESSION.sql_mode` после WordPress bootstrap. Поэтому на обычной default WP-сессии apply намеренно fail closed. Требуется отдельно согласованный и проверенный session profile на изолированном стенде; плагин не меняет `sql_mode` общей сессии автоматически и не ослабляет строгий preflight ради запуска.
SQL identifiers формируются только из trusted wpdb whitelist и проходят `%i`; внешние значения — только native bound parameters. Узкий `SM_V2_SQL` позволяет инъецировать transport; production `SM_V2_Mysqli` в `includes/v2-sql.php` использует текущий WP connection, без reconnect/retry. Константные SET/START/COMMIT/ROLLBACK выполняются отдельно без строк из запроса. `wpdb::query()` намеренно не используется: его автоматический reconnect+SQL replay может разрушить атомарность после потери соединения.
После START SERIALIZABLE транзакция удерживает metadata locks затронутых таблиц и повторно проверяет движки, индексы, ledger schema и обнаруженные triggers. Unique operation row резервируется до target lock. Existing same hash возвращает подписанный receipt, другой hash — 409. Неожиданная ранее committed NULL reservation — `needs_reconciliation`, а не повторное применение.
Затем `posts … FOR UPDATE` блокирует target; locking read postmeta по post_id защищает существующие строки и отсутствующие managed values через InnoDB next-key locks. Более 1024 meta rows, неоднозначные/дублированные managed keys, malformed/слишком большие значения — fail closed.
Под теми же locks проверяются before digest, весь допустимый after, expected-after digest. Записываются только действительно отличающиеся managed meta. Readback проверяет точные bytes/digest; SQL truncation/escaping/mixed state не выдаются за applied. Метаданные и terminal signed receipt коммитятся вместе.
WP `update_post_meta`/`wp_update_post`, revisions, publication hooks не вызываются. Native bindings сохраняют JSON/backslashes без лишнего `wp_unslash`/`wp_slash`; последующая WP meta cache invalidation — только после commit, в том числе при status/retry recovery. Это гарантия управляемых DB-полей, **не global exactly-once** сторонних hooks/уведомлений/произвольных plugins или replication failover.
SQL/write/postcondition failure вызывает rollback. Неопределённый COMMIT, ошибка rollback, неизвестный receipt или повреждённое состояние возвращают `needs_reconciliation`, 503. Потеря соединения не маскируется успехом. Lock wait/deadlock — безопасный конечный failure; caller может выполнить bounded retry только того же operation id/body с новым transport nonce.
Квитанция исходного ответа и recovered receipt сериализуются одинаковым canonical порядком. Между COMMIT и HTTP/cache invalidation возможен сбой: это не основание отправлять новую логическую операцию. Внешние page/CDN caches и public HTML проверяются отдельно.
## Receipt и восстановление потерянного ответа
Receipt связывает protocol/kind, receipt id, operation/project/site/entity/task/proposal ids, proposal hash, approval reference, raw request hash, before/after snapshots+digests, plugin version/key id, UTC applied_at, реально изменённые поля, outcome и warnings.
Outcomes: `applied_unverified` либо `no_op` (не увеличивает логические мутации). Ни receipt, ни HTTP 200 не означают `verified`.
Receipt key = binary HMAC-SHA256 от ASCII `SEO-MASTER-RECEIPT-KEY-V2` с отдельным ASCII receipt secret. Signature = hex HMAC-SHA256 от `SEO-MASTER-RECEIPT-V2` + LF + canonical receipt **без signature**, с полученным binary key. Challenge использует тот же выделенный signing context, но имеет `kind:"challenge"`, echo 64-hex challenge, exact project/site/key, UTC issued/expires (60 секунд); Gateway обязан проверить kind/scope/challenge/expiry/signature.
При timeout caller запрашивает `GET /operations/{same id}` с новым nonce. После nonce claim status выполняет собственные SERIALIZABLE begin/read/commit и locking `SELECT … FOR UPDATE`; actual store запрещает receipt reads вне транзакции. Нельзя унаследовать READ UNCOMMITTED и вернуть receipt другой ещё не зафиксированной операции. Найденный receipt повторно проверяется плагином по scope/request/signature. При 404 сначала сверяется snapshot; допустим retry **того же id/body**, не новая операция. `needs_reconciliation` — явный ручной/управляемый выход, не разрешение blindly replay.
Ledger/tombstones автоматически не очищаются; старый operation id не должен снова становиться новым. В этом инкременте также нет автоматической очистки nonce; обслуживание retention/size должно быть согласовано отдельно. Storage outage не включает небезопасный cache fallback.
## Установка схемы — только отдельно разрешённый стенд
Обычная активация и REST **не выполняют DDL**. Без таблиц v2 операции закрыты.
Код создания двух новых таблиц находится в `SM_V2_WPDB_Store::install_schema`. Он вызывается activation hook только при явном `SEO_MASTER_INSTALL_V2_SCHEMA=1` и административных capabilities. На пользовательском/общем WP и БД в рамках AR-5 его запуск запрещён. Перед разрешённым стендом нужны отдельное согласование, изолированные fixtures и проверенные права; после установки флаг убрать.
Не выполняется ALTER существующих posts/postmeta, автоматическая конвертация движков, destructive reset или удаление ledger. Uninstall пока сохраняет v2 ledger: безопасная decommission/purge-политика — отдельная задача.
## Локальные проверки
CI выполняет тот же standalone gate отдельным job на Ubuntu 24.04 с системным `/usr/bin/php8.3`, без запуска MySQL/WordPress, установки плагина и DDL. Наличие PHP 8.3 сверено по опубликованному inventory `actions/runner-images`; patch-версия зависит от обновления runner-образа и не считается зафиксированной compatibility matrix. Отсутствующий executable или любой провал завершает job ошибкой. Строгая проверка Python использует `mypy --strict --explicit-package-bases`: флаг однозначно задаёт namespace packages для статического импорта `tests.wordpress.run`, не отключая проверки типов.
Из корня worktree: `python -B tests/wordpress/run.py --php C:\Users\user\Downloads\php\php.exe`. Только синтаксис: та же команда с `--lint-only`.
Runner использует Python 3.11+ stdlib, явный PHP path, `php -n`, очищенное окружение дочерних процессов, тайм-аут и ненулевой exit при любом провале/отсутствующем harness. Не читает `.env`, не устанавливает пакеты, не делает запросов к сети/БД и не использует skips/xfails.
Source gate проверяет PHP непосредственно в корне плагина, в `admin/`, `includes/` и `tests/wordpress/`, включая дополнительные canonical source files. Обязательны все 17 текущих PHP-файлов: отсутствие любого из них или каталог вместо файла приводит к hard failure, независимо от общего количества найденных файлов. Во вложенных каталогах `dist/`, `generated/`, `vendor/` PHP не читается и не выполняется как canonical source.
Portable discovery regression: `python -B -m unittest discover -s tests/platform -p test_wordpress_runner.py -v`. Тест использует временные source fixtures и stub вызова PHP, не подменяет native PHP suite или проверку пакета. Исключение packaging artifacts из source gate **не делает старый dist/ZIP безопасным**: AR-15 artifact gate остаётся красным; legacy artifact не пересобран, не проверен и не допускается как v2 release.
До исправлений `legacy-regression.php` падал на всех пяти дефектах: unknown op success, post_title mutation, replacement Yoast/RankMath graph, JSON-LD script escape. Контракты включают replay, scope/signature, dry-run unchanged, unknown operation zero writes, CAS, 10 повторов same op, different request hash 409, failure rollback, lost/ambiguous commit response и durable receipt recovery через test store. Дополнительные проверки покрывают pairing/admin nonce/routes и общие vectors.
## Обязательные незакрытые gates
До заявления реальной работоспособности/завершённости AR-5 требуются:
1. Независимое source review ограниченного core и discovery runner выполнено другим автором: исходные JSON-kind/status findings закрыты регрессиями, PHP/portable/strict gates повторены после интеграции. Это не закрывает остальные runtime/приёмочные gates списка.
2. Разрешённый настоящий WordPress + MySQL/InnoDB стенд: activation/schema/preflight, реальные REST HMAC/challenge/snapshot/dry-run/apply/status и отсутствие post_title/content/revisions mutations.
3. Десять параллельных same-op requests, same key/different hash, два op на один target со stale before, параллельный WP/manual meta insert/update/delete, отсутствующие meta rows и duplicate-key races. Проверка ledger/bytes/количества логических мутаций, не только HTTP.
4. Kill PHP worker после первого поля и до ledger/COMMIT, потеря соединения, lost COMMIT/HTTP response, рестарт БД; проверка atomic before-or-after, receipt/status и явного reconciliation при неизвестности. Отдельный двухсессионный сценарий: исходная WP-сессия READ UNCOMMITTED, другая удерживает `save_operation` до COMMIT/ROLLBACK; status не должен отдавать held receipt до commit и не должен возвращать откатившийся receipt. Проверка фактических next-key/metadata locks и настроек durability на выбранных версиях.
5. MyISAM/mixed engines, utf8/strict-mode/schema/index mismatch, existing foreign transaction и несовместимые triggers/drop-ins: отказ до мутаций. DB trigger visibility/права и запрет одновременного административного DDL должны быть подтверждены стендом.
6. Независимый публичный HTML verifier, core theme + Yoast/RankMath title/description matrix, отсутствие скрытых дублей/кэш-расхождений; JSON-LD integration с SEO-плагинами остаётся manual до отдельного подтверждения.
7. AR-4 trusted Gateway authorization/ownership/HITL envelope и переход клиента с v1; реальный rollback endpoint с собственным approval/CAS/receipt пока не реализован и не рекламируется.
8. AR-15 полный подписанный release package, trust bootstrap, ZIP tamper/path traversal tests; ingress limits/TLS и согласованная retention/decommission эксплуатация.
Эти gates не заменяются успешным standalone harness. Промежуточный diff/коммит не означает готовность RC.
## Проверенные источники API
- [wpdb::query: reconnect/replay и query hooks](https://developer.wordpress.org/reference/classes/wpdb/query/).
- [wpdb::__get: доступ к текущему dbh](https://developer.wordpress.org/reference/classes/wpdb/__get/).
- [wpdb::prepare и identifier %i](https://developer.wordpress.org/reference/classes/wpdb/prepare/).
- [PHP mysqli prepared bind_param](https://www.php.net/manual/en/mysqli-stmt.bind-param.php).
- [MySQL InnoDB locking reads](https://dev.mysql.com/doc/refman/8.4/en/innodb-locking-reads.html).
- [MySQL transaction characteristics и запрет nested SET TRANSACTION](https://dev.mysql.com/doc/refman/8.4/en/set-transaction.html).
- [MySQL metadata locks](https://dev.mysql.com/doc/refman/8.4/en/metadata-locking.html).
- [WP raw metadata read вызывает maybe_unserialize](https://developer.wordpress.org/reference/functions/get_metadata_raw/).
- [wpdb::set_sql_mode и WordPress incompatible modes](https://developer.wordpress.org/reference/classes/wpdb/set_sql_mode/).
- [MySQL TRIGGERS metadata требует TRIGGER privilege](https://dev.mysql.com/doc/refman/8.4/en/information-schema-triggers-table.html).
