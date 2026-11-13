# Semantic service: типизированные предложения, FF-Score и E-E-A-T (AL-5)

Документ описывает поведение `services/semantic_service` после AL-5. Отдельных контрактных файлов в `contracts/` нет: схема предложения живёт в коде semantic (`proposals/model.py`) и отдаётся запросом `GET /semantic/schemas/recommendation-version`. Согласование и исполнение (AR-3, AR-4, AR-6) читают схему оттуда, своей копии не заводят.

Все оценки в этом документе — эвристики приложения. Они не прогнозируют позиции, трафик или выручку и не доказывают причинную связь.

## 1. RecommendationVersion

Неизменяемая версия предложения об изменении одной страницы. Модель — `proposals/model.py`; поля, не описанные в схеме, отвергаются (`extra="forbid"`), неизвестный тип операции тоже.

| Поле | Смысл |
|---|---|
| `schema_version` | `recommendation_version/1` |
| `version_id`, `recommendation_id` | UUID версии и UUID самого предложения; у всех версий одного предложения `recommendation_id` один |
| `version`, `previous_version_id` | номер с 1; `previous_version_id` обязателен ровно при `version > 1` |
| `project_id`, `task_id` | проект (UUID) и необязательная задача |
| `target` | `origin`, `url` (каноническая форма), необязательная внешняя сущность `entity {kind, id}` (например, запись WordPress) |
| `evidence` | ссылки на данные аудита: `evidence_id` + `sha256`; не меньше одной |
| `operation_type` | `title`, `description`, `h1`, `schema_jsonld`, `content_edit`, `internal_link` |
| `execution_mode` | `managed` — есть управляемая запись через CMS-механизм; `manual` — только ручная задача, автоматическая запись не заявляется |
| `manual_reason` | обязателен ровно для `manual`: `cms_plugin_has_no_h1_write`, `cms_plugin_has_no_content_write`, `cms_plugin_has_no_internal_link_write` |
| `changes[]` | изменения; см. ниже |
| `preconditions[]`, `expected_checks[]` | выводятся из `changes`, а не пишутся вручную; валидатор сравнивает с повторным выводом |
| `limitations[]` | честные ограничения, например `before_observed_on_page_not_cms_snapshot` |
| `provider`, `model`, `prompt_version`, `generator_version`, `validator_version`, `provenance[]` | кто и чем сгенерировал; по каждому полю `source` = `llm`, `cache` или `deterministic` |
| `created_at`, `content_hash` | время и SHA-256 от канонического JSON существенных полей |

`changes[]`: `path`, `mode`, `before_state`, `before`, `after`, `cms_field`.

- `mode`: `set` (установить `after`), `remove` (явно удалить), `unchanged` (явно не менять). Пустое значение никогда не подставляется вместо отсутствующего: `after` задан только у `set`, у `remove` и `unchanged` он `null`.
- `before_state`: `missing` (поля нет, `before = null`) или `value` (значение есть, `before` обязателен; пустая строка — это значение).
- Допустимые пути: `head.title`, `head.meta_description`, `body.h1`, `head.jsonld`, `body.content_block:<id>`, `body.internal_link:<n>`. Остальные отвергаются.
- `cms_field` — поле команды WordPress-плагина v2 (`title`, `description`, `jsonld`) либо `null`, если у плагина такого поля нет (h1, контент, внутренние ссылки).
- Значение JSON-LD — каноническая JSON-строка, как её принимает плагин: `{"op":"set","field":"jsonld","value":"<JSON string>"}`; только базовые ключи `@context`, `@type`, `name`, `description`.

`content_hash` не включает идентификаторы, номер версии, время и `generated_at`/`source` в `provenance`: повторная генерация того же содержимого тем же провайдером даёт тот же хеш и не создаёт новую версию.

### Версии и идемпотентность
Хранилище только добавляет версии. Если содержимое совпало с последней версией — статус `existing`, новая не создаётся. Изменилось — создаётся `version + 1` со ссылкой на предыдущую, предыдущая остаётся в истории. Параллельные запросы разрешаются уникальными ограничениями БД: проигравший перечитывает последнюю версию и повторяет попытку (до трёх раз, затем `409 version_conflict`).

### Что проверяет валидатор
Один и тот же валидатор проверяет ответ LLM, значение из кэша, значение детерминированного пути и готовую версию:
длины (title 10–70, description 50–160, h1 5–120 знаков), управляющие и скрытые символы, HTML, заглушки («Официальная страница», «Заголовок страницы», голый адрес), неподтверждённые утверждения («лучший в…», «гарантия», «100 %», «сертифицирован», «бесплатно» и т. п. — разрешены только если есть в фактах страницы), корректность JSON-LD, совпадение `preconditions`/`expected_checks` с выведенными, `cms_field` и `execution_mode` с возможностями плагина, пересчитанный `content_hash`.

## 2. API

| Метод | Назначение |
|---|---|
| `POST /semantic/proposals` | создать предложения для запрошенных типов |
| `GET /semantic/proposals/{version_id}` | версия по идентификатору (при повреждённой строке — `500 stored_version_corrupted`) |
| `GET /semantic/recommendations/{recommendation_id}/versions` | история версий предложения |
| `GET /semantic/projects/{project_id}/proposals?limit=` | последние версии проекта |
| `GET /semantic/schemas/recommendation-version` | JSON Schema модели |
| `POST /semantic/ff-score`, `POST /semantic/eeat`, `GET /semantic/latest/{project_id}` | оценки (раздел 3) |
| `POST /semantic/drafts` | **снят**: `410 drafts_endpoint_removed`, замена — `POST /semantic/proposals` |

`POST /semantic/proposals`: тело — `project_id`, `target_url`, `evidence[]`, `facts` (title, description, h1[], text, lang, site_name, organization_name, jsonld_present, jsonld), `finding_codes[]` или явный `operations[]`, `schema_type`, необязательные `task_id`, `entity`. Если `operations` не указан, типы выводятся из кодов находок аудита (`title_missing`, `description_too_long`, `h1_missing`, `jsonld_missing` и т. д.). Автоматически генерируются `title`, `description`, `h1`, `schema_jsonld`.

Ответ — по элементу на каждый тип со статусом:

- `created` — новая версия;
- `existing` — то же содержимое уже последняя версия;
- `no_change` — предложенное значение совпадает с текущим;
- `needs_input` — фактов не хватает или кандидат отвергнут валидатором: `needs_input.missing` (что нужно собрать), `reason`, `issues[]` (коды причин). Шаблонный текст вместо этого не подставляется.

Ошибки: `422 proposal_rejected` с `issues[]` (например, `no_operation_requested`, `target_url_invalid`), `422` валидации тела, `409 version_conflict`.

### Правила данных
- `before` берётся из фактов страницы (результат аудита), а не из снимка CMS. Это записано в `limitations` (`before_observed_on_page_not_cms_snapshot`); перед записью плагин сверяет `before_digest` с реальным значением (CAS по предусловию).
- Если текущее значение неизвестно (например, `jsonld_present` не передан), предложение не создаётся: `needs_input` с `missing = jsonld_presence`.
- Несколько `h1` на странице — `needs_input` (`multiple_h1_manual_review`): какой оставить, решает человек.
- Что может записать плагин v2 (`docs/WORDPRESS.md`): `title`, `description`, `jsonld`. `h1`, `content_edit`, `internal_link` — `execution_mode = manual`; это не «ошибка записи», а честная ручная задача. Генерация `content_edit` и `internal_link` автоматически в AL-5 не выполняется (внутренняя перелинковка — AL-14); тип и проверки в схеме уже есть.

### Отображение для AR-3 / AR-4
Для команды плагина берутся `changes[i].cms_field`, `mode` и `after`: `set` → `{"op":"set","field":cms_field,"value":after}`, `remove` → `{"op":"remove","field":cms_field}`. Предусловия: `before_digest` (SHA-256 точной UTF-8 строки `before`; `state = missing` означает, что поля нет), `evidence_hash`, `target_in_project`. Ожидаемая проверка после записи: `field_equals_after` (SHA-256 от `after`), `field_removed`, `target_http_ok`. Хеш предложения для `proposal_hash` — `content_hash`; `proposal_version` — `version`.

## 3. FF-Score и E-E-A-T

### Принципы
- Формулы компонентов и веса не менялись. При полном наборе измеренных входов результат совпадает с прежним (проверено контрольными значениями в тестах).
- Недостающий вход больше не заменяется числом (раньше: 50, 30 дней, 0 и т. п.). Он остаётся `unavailable` с причиной.
- Значения с доступностью `estimated`, `proxy`, `mock`, `sample` показываются, но в полный балл не входят. В полный балл входят `measured`, `provided`, `derived`.
- Полный балл при недоступном компоненте — `null` со статусом `insufficient_data`.
- Частичный балл — отдельная формула `ff_score_partial_v1` / `eeat_partial_v1`: считается только по доступным компонентам, веса пересчитаны явно (`weights_applied`), указано покрытие (`coverage`) и `comparable_to_full_score: false`. Его нельзя сравнивать с полным.
- Пороги `< 40` и `> 60` — эвристические режимы (`rescue`, `maintain`, `growth`), не прогноз роста. Режим есть только у полного балла.

### ff_score_v1
`ff_score = 0.30·freshness + 0.40·familiarity + 0.30·quality`.

| Компонент | Входы (единицы) |
|---|---|
| freshness | `freshness_days_since_update` (дни, 0…3650), `serp_shift` (позиции, −50…50), `link_velocity` (0…100) |
| familiarity | `semantic_distance` (%, 0…100), `keyword_coverage` (%, 0…100), `eeat_score` (0…100, только полный `eeat_v1`) |
| quality | `cwv_grade` (`good`/`needs_improvement`/`poor`), `broken_links_count`, `schema_errors_count` |

Правила сборки входов (`scoring/assemble.py`):
- Метка источника `estimate`/`mock`/`sample`/`proxy` или суффикс `_degraded` понижает доступность значения; вызывающий может указать доступность явно в `input_availability`.
- `semantic_distance` и `keyword_coverage` из сохранённого анализа недоступны, если анализ шёл без SERP-текстов или без ключей: прежний пайплайн возвращал для них заглушки 100 и 0.
- `cwv_grade` — худшая из LCP, INP, CLS по данным аудита. Если одной метрики нет, оценка считается прокси и в полный балл не входит. FID не подменяет INP.
- `audit_findings: null` — находки не передавались (счётчики недоступны); `[]` — аудит прошёл, находок нет (счётчики равны 0).
- Счётчики `backlinks_count`, `brand_mentions`, `authoritative_outbound_links`, равные 0 и без указания источника в `input_sources`, считаются «не проверялось», а не «нет».

Ответ `POST /semantic/ff-score`: `formula_version`, `status` (`complete`/`insufficient_data`), `ff_score` (число или `null`), `components` (по каждому: `status`, `score`, `weight`, `inputs`, `missing_inputs`, `missing_reasons`), `weights`, `thresholds`, `mode`, `coverage`, `partial`, `heuristic`, `note`, `inputs` (по каждому входу: `value`, `unit`, `availability`, `source`, `observed_at`, `reason`), `eeat`.

> Следствие: пока для `link_velocity`, `serp_shift` и данных свежести нет реального источника (GSC/GA4/Яндекс — AL-9, AL-13), полный FF-Score будет `null`. Частичный балл при этом считается и показывается отдельно.

### eeat_v1
Веса 0.25 experience / 0.30 expertise / 0.25 authoritativeness / 0.20 trustworthiness. Сигнал `None` — «не проверялось». Компонент недоступен, если неизвестен любой его сигнал; `breakdown` содержит `null` для недоступных компонентов; `missing_inputs` называет недостающие сигналы. `has_https` выводится из схемы адреса. Это внутренняя эвристика, а не оценка Google.

### Хранение
`semantic_schema.ff_scores` и `eeat_scores`: `score` допускает NULL, добавлены `status` и `formula_version`. CHECK: `(status = 'insufficient_data') = (score IS NULL)`, диапазон 0…100 (NaN и Infinity отвергаются), статус из `complete`, `insufficient_data`, `legacy_estimated`. Строки, записанные до AL-5, получили `status = 'legacy_estimated'`, `formula_version = 'legacy'`: их баллы считались с подстановкой оценок, API возвращает их с признаком `legacy_estimated` и пояснением.

### Событие FFScoreRecalculated, schema_version 2 (`ffscore_recalculated/2`)
Маршрут `semantic.ffscore.recalculated`. Изменения относительно версии 1:
- `ff_score` — число 0…100 или `null`; `null` ровно при `status = insufficient_data`;
- добавлены `status`, `formula_version`, `schema_version`;
- `eeat_score` — число 0…100 или `null`; `eeat` — тот же число-или-`null` (алиас прежнего имени), больше не объект. Раньше потребитель, читавший `eeat` как число, мог сохранить объект в `current_eeat`;
- подробности E-E-A-T — в объекте `eeat_details` (`score_id`, `status`, `score`, `breakdown`, `partial`, `missing_inputs`);
- NaN, Infinity и значения вне 0…100 событие не пропускает.

Reporting-обработчик сохраняет `eeat` как есть; чтобы сохранять и разбивку, ему нужно читать `eeat_details` (в AL-5 не менялся).

### Потребитель: API проектов и панели (api_gateway)

Код: `services/api_gateway/score_view.py`. Блок `ffScore` в `GET /api/projects` и `GET /api/projects/{id}`: `total` (число или `null`), `status` (`complete | insufficient_data | legacy_estimated`), `formulaVersion`, `heuristic`, `legacyEstimated`, `freshness` / `familiarity` / `quality` (число или `null`; у новой формулы берётся `score` доступного компонента), `partial`, `coverage`, `timestamp`. Недоступное значение остаётся `null`, нулём не заменяется. `GET /api/dashboard/stats`: `avgFFScore` — среднее последних баллов проектов только со статусом `complete` (оценочные `legacy_estimated` и `null` не участвуют), `ffScoreProjects` — число учтённых проектов; без таких проектов `avgFFScore = null`.

## 4. LLM-слой

Код: `llm/policy.py`, `llm/providers.py`, `llm/llm_cache.py`, `llm/llm_client.py`.

- Провайдеры вызываются по очереди (OpenAI-совместимый, затем Gemini). Первый ответ, принятый валидатором, побеждает. Ошибка, таймаут или отвергнутый ответ одного провайдера не прекращает попытки: следующий провайдер и затем детерминированный путь по фактам страницы. Прежняя утечка задач (`asyncio.wait` с `FIRST_COMPLETED`) и преждевременный fallback убраны.
- Таймаут на провайдера: 5 с для простых запросов, 15 с для сложных, не дольше общего бюджета на запрос. Просроченный вызов отменяется и дожидается отмены (`wait_for`).
- Исключения провайдеров изолированы и типизированы (`ProviderError.code`); ключи и тексты исключений в логи и ответы не попадают.
- Ключ кэша: `sha256` от (`scope` проекта, хеш входных данных, провайдер, модель, версии prompt / schema / validator, параметры запроса), TTL 7 дней. Без `scope` кэш не используется. Значение из кэша повторно проходит валидатор. Недоступный Redis — промах кэша, а не ошибка.
- Данные страницы передаются между маркерами `<page_content>`, управляющие символы и маркеры вычищаются, объём ограничен 6000 знаками; это снижает, но не исключает prompt injection, поэтому любой ответ проходит валидатор.

## 5. Миграция `20261104_001_semantic_al5`
Накат: nullable-баллы, `status`/`formula_version` с заполнением `legacy_estimated`/`legacy` для существующих строк, CHECK-ограничения, таблица `semantic_schema.recommendation_versions` (уникальные `(recommendation_id, version)` и первая версия на `(project_id, target_url, operation_type)`, триггер запрета UPDATE). Данные не удаляются.

Откат отказывает без изменений, если есть строки с `NULL`-баллом (NOT NULL восстановить нельзя) или сохранённые версии предложений (откат стёр бы историю). Схема зеркалируется в `database/init_schema.sql`, `database/models.py` и `scripts/migration_policy.py` (`CORE_TABLES`).

## 6. Проверка
`tests/unit/test_ff_score_calculator.py`, `test_eeat_analyzer.py`, `test_llm_policy.py`, `test_semantic_proposals.py`, `test_semantic_proposals_extra.py`, `test_semantic_scoring_api.py` входят в профиль `semantic` и работают без сети и БД. `tests/integration/test_semantic_postgres.py` и `test_migrations_postgres.py` требуют PostgreSQL (`TEST_PG_ADMIN_URL`) и без него пропускаются.

## 7. Известные ограничения
- Полный FF-Score `null`, пока нет источников свежести, сдвига SERP и динамики ссылок.
- `before` берётся из аудита страницы, а не из снимка CMS; точная сверка — на стороне плагина по `before_digest`.
- Management-оркестратор (`_wait_for_scores_completion`) считает `ff_score is None` ошибкой, а `/semantic/drafts` больше не существует: переход на `ff_score = null` и `/semantic/proposals` — AR-3.
- Автоматическая генерация `content_edit` и `internal_link` не реализована (AL-14); схема и проверки готовы.
