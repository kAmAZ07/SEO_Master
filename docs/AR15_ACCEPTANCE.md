# AR-15: подписанный пакет и native field matrix AR-5

Исходная ревизия: `main` `5dd235f551c0c1bad16072240883c0441aae1b4d` (PR #110). Ветка `arseniy/ar-15-signed-plugin`. Требование: REQ-WP §11.5, primary AC-08; дополнительная проверка существующих полей AR-5 по §11.4. Работа не зависит от новых upstream-контрактов Александры. Небольшой стык выдачи ZIP в API требует её owner review перед merge.

## Что изменено

`shared/plugin_package.py` задаёт единственный ZIP wire format и внешний Ed25519 verifier. `scripts/plugin_package.py` читает blobs одного commit, нормализует только CRLF в LF, проверяет exact inventory/modes/версию и создаёт новый воспроизводимый ZIP. Изменение рабочей копии не подменяет source_commit. PHP проверяет подпись канонического manifest и каждый файл до остальных includes; activation hook зарегистрирован до fail-closed return. CMS callbacks повторно проверяют целостность. Старый optional SHA и неподписанный ZIP 0.2.0 удалены.

API требует явный artifact path, archive SHA-256 и внешний public-key map. Не выбирает latest по mtime и не принимает ключ из ZIP. Читает bounded regular-file descriptor (POSIX O_NONBLOCK защищает от FIFO), проверяет и отдаёт тот же snapshot, без повторного открытия mutable path. Без настроенного пакета — 503. В базовом образе остаётся только dist/README; ZIP предоставляется явно read-only.

## Трассировка и фактические проверки

Проверено 14.11.2026 на Windows, Python 3.11.9, в изолированных окружениях из test-lock; Docker Desktop Linux, закреплённые WordPress 6.8.3/PHP8.3 и MariaDB10.11. Source PHP gate локально использовал PHP8.5.4 с явным sodium, `-n` и отключённым ctype_digit; это не доказательство PHP8.0 compatibility.

| Требование → AC | Проверка | Фактическое доказательство и статус |
| --- | --- | --- |
| §11.5 → AC-08: asymmetry, pinned key, все executable | `tests/unit/test_plugin_package.py`; `package-contract.php` | Настоящие Ed25519/cryptography и sodium. Все 15 payload-файлов, canonical manifest с изменённым commit, canonical envelope с неверной 64-byte подписью, missing metadata/trust, extra files отклонены. PHP: 47 assertions PASS. |
| §11.5 → AC-08: ZIP layout и budgets | `test_plugin_package.py` | Traversal/absolute/drive/dot aliases, duplicate/symlink, Unicode extras, local-header mismatch, comments, preamble/trailing, oversize и untrusted self-signed package отклонены до extraction. Не утверждается исчерпывающий mutation corpus. |
| §11.5 → AC-08: воспроизводимость и provenance | Git fixture tests `test_build_reads_exact_commit_not_dirty_checkout`, `test_unapproved_git_inventory_rejected` | Dirty/untracked bytes не меняют подписанный результат exact commit; unlisted executable и неправильные Git modes/missing payload запрещены. Native CI дополнительно собирает реальные blobs `--plugin-revision HEAD`. |
| §11.5 → AC-08: безопасная выдача | `test_download_only_explicit_verified_snapshot` | Exact byte response даже после замены path непосредственно после verification; invalid digest/key/config/file дают 503. Чистый полный API profile: **600 passed**, 82 существующих deprecation warnings. |
| §11.5 → AC-08: настоящий устанавливаемый ZIP | `NativeWordPress` в `tests/wordpress/native.py` | Настоящий Plugin_Upgrader install, activation, upgrade и reactivation; после обновления tree signature valid. Tampered candidate отклонён с WP source_selection error, marker его исполнения отсутствует, hashes старого дерева сохранены, оно реактивировано. Fresh-process missing-trust activation отказала, active_plugins не содержит неподтверждённую активацию. PASS. |
| §11.4 → AC-08: поля/состояния/точный before | `tests/wordpress/field_matrix.py` | **12 native cases**: post/page × missing/empty/Unicode × set/remove; title/description/JSON-LD, подписанные receipt/status, persisted meta, anonymous public HTML, exact rollback. post_title/content не изменились. PASS на core theme. Это прямой CMS contract, без заявления о Semantic generation или browser approval. |
| Регрессия CMS/HITL | `native_contract --with-management --with-apps` | Полный локальный прогон с новым signed installer прошёл: настоящий REST, CAS/lock deadline, конкурентный same-op; мигрированная authority; API/Management/Client UID10001/read-only/no-DDL; реальные post-CMS-commit SIGKILL для apply/rollback, recovery и независимое public verify. Последующие отрицательные native cases/field matrix также прошли отдельным прогоном. |
| Обязательные platform/source gates | platform unittest, mypy/ruff, PHP runner | **97 platform tests PASS** вне sandbox (в sandbox четыре ACL fixtures получили отказ ОС); strict mypy 18 файлов и ruff PASS; PHP lint 19 файлов и все прежние harness/vectors PASS. Общий migration gate не открывался. |

Source review выполнено ранее разрешённым независимым reviewer `/root/ar4_review`: выявленные early-return activation и special-file blocking исправлены. Reviewer не запускал тесты и не подтверждал staging/AC целиком; native evidence приведено отдельно. Полный обязательный CI и owner review остаются условием merge.

## Воспроизведение

```text
python -B scripts/backend.py check api
python -B -m unittest discover -s tests/platform -v
python -B -m tests.wordpress.run --php <explicit-php-with-sodium>
python -B -m tests.wordpress.native_contract --docker-context <context> --plugin-revision HEAD --with-management --with-apps
```

Последняя команда требует management-test lock и создаёт только собственный помеченный одноразовый стенд. Все тестовые signing/HMAC/receipt keys генерируются в памяти, release private key/ZIP не коммитятся. Без `--plugin-revision` native тест помечает working-source fixture all-zero source_commit; это не release provenance. Baseline для update — отдельный синтетический пакет с изменённым readme, не доказательство миграции всех исторических версий плагина.

## Границы приёмки

- Внешняя проверка обязательна **до** extraction/первого PHP исполнения. Проверка внутри пакета не доказывает безопасность полностью скомпрометированного PHP host/verifier/trust config. Штатный manual updater может деактивировать старый плагин до внутреннего source_selection отказа; после намеренного обхода внешней проверки требуется реактивация проверенного старого дерева. Это показано тестом, сохранение непрерывной активности не заявляется.
- Подпись подтверждает происхождение/целостность, не «самую новую» версию. Artifact digest и разрешённые ключи выбирает оператор вне ZIP; protected release key, ротация/отзыв и реальное распространение настраиваются отдельным release-процессом. Production key, release/tag/deploy здесь не создавались.
- Полная theme/Yoast/RankMath/WordPress matrix — AR-17; H1/links — AR-12; browser E2E — AR-9. Успешные native проверки выше не означают RC1.0 или полное выполнение прежнего ТЗ. Shared startup/migrations и migration gate не обходились.
- Актуальная инструкция эксплуатации и trust bootstrap: [PUBLISHING.md](../adapters/wordpress-plugin/PUBLISHING.md).
