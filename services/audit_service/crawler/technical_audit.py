import asyncio
import ipaddress
import socket
from typing import Type
from urllib.parse import urlparse

from sqlalchemy import select

from services.audit_service.crawler.public_crawler import crawl_public
from services.audit_service.analyzers.meta_checker import check_meta, check_canonical
from services.audit_service.analyzers.link_checker import check_links_404
from services.audit_service.analyzers.schema_validator import validate_jsonld
from services.audit_service.analyzers.robots_checker import check_robots
from services.audit_service.analyzers.sitemap_checker import check_sitemap
from services.audit_service.analyzers.cwv_analyzer import analyze_cwv
from services.audit_service.config import settings
from services.audit_service.db.session import get_session
from services.audit_service.db.models import CrawlResult, PublicAuditResult


FINDING_CATALOG = {
    "invalid_url": {
        "title": "Некорректный URL аудита",
        "description": "Целевой URL некорректен или не содержит обязательной схемы и хоста.",
        "recommendation": "Используйте полный публичный URL, начинающийся с http:// или https://.",
        "category": "precheck",
    },
    "unsafe_target": {
        "title": "Приватный или внутренний адрес",
        "description": "Запрошенный хост разрешается в приватный, loopback- или link-local-адрес.",
        "recommendation": "Запускайте аудит для публично доступного домена, а не для внутреннего адреса.",
        "category": "precheck",
    },
    "blocked_by_robots": {
        "title": "Сканирование заблокировано robots.txt",
        "description": "Корневой путь запрещён для обычных crawler'ов, поэтому аудит не может безопасно проверить сайт.",
        "recommendation": "Разрешите crawler'у доступ или запустите аудит в среде без ограничений robots.txt.",
        "category": "crawlability",
    },
    "sitemap_missing": {
        "title": "Sitemap отсутствует или недоступен",
        "description": "Crawler не смог найти корректный файл sitemap.xml на целевом сайте.",
        "recommendation": "Опубликуйте корректный sitemap.xml и убедитесь, что он доступен без авторизации.",
        "category": "crawlability",
    },
    "title_missing": {
        "title": "Отсутствует тег title",
        "description": "На странице нет тега title, поэтому поисковики не получают явного названия страницы.",
        "recommendation": "Добавьте уникальный и информативный тег title для страницы.",
        "category": "metadata",
    },
    "title_too_short": {
        "title": "Тег title слишком короткий",
        "description": "Title присутствует, но слишком короткий, чтобы чётко описать назначение страницы в результатах поиска.",
        "recommendation": "Расширьте title приблизительно до 10–70 символов, сохраняя конкретность и читаемость.",
        "category": "metadata",
    },
    "title_too_long": {
        "title": "Тег title слишком длинный",
        "description": "Title, вероятно, будет обрезан в результатах поиска и может размыть основную тему.",
        "recommendation": "Сократите title приблизительно до 10–70 символов, разместив ключевую тему ближе к началу.",
        "category": "metadata",
    },
    "description_missing": {
        "title": "Отсутствует meta description",
        "description": "На странице нет мета-описания, поэтому поисковики могут генерировать неконтролируемый сниппет.",
        "recommendation": "Добавьте краткое мета-описание, раскрывающее ценность и назначение страницы.",
        "category": "metadata",
    },
    "description_too_short": {
        "title": "Meta description слишком короткий",
        "description": "Описание слишком короткое, чтобы предоставить достаточный контекст для привлечения целевых кликов из поиска.",
        "recommendation": "Расширьте мета-описание приблизительно до 50–160 символов.",
        "category": "metadata",
    },
    "description_too_long": {
        "title": "Meta description слишком длинный",
        "description": "Описание может быть обрезано в поисковых сниппетах, что ослабляет сообщение для пользователей.",
        "recommendation": "Сократите описание приблизительно до 50–160 символов, разместив ключевое предложение ценности в начале.",
        "category": "metadata",
    },
    "canonical_missing": {
        "title": "Отсутствует тег canonical",
        "description": "На странице нет тега <link rel=\"canonical\">, поэтому поисковики самостоятельно определяют предпочтительный URL.",
        "recommendation": "Добавьте самореферентный тег canonical на каждую страницу, чтобы однозначно указать предпочтительный URL.",
        "category": "metadata",
    },
    "canonical_multiple": {
        "title": "Несколько тегов canonical",
        "description": "На странице найдено более одного тега <link rel=\"canonical\">. Поисковики учитывают только первый; остальные игнорируются и создают путаницу.",
        "recommendation": "Оставьте ровно один тег canonical на странице, указывающий на предпочтительный URL.",
        "category": "metadata",
    },
    "canonical_mismatch": {
        "title": "URL canonical не совпадает с URL страницы",
        "description": "Тег canonical ссылается на URL, отличающийся от проверяемой страницы. Это сигнализирует поисковикам, что текущая страница не должна индексироваться.",
        "recommendation": "Убедитесь, что canonical URL указан намеренно. Если страница должна индексироваться по собственному URL, обновите canonical на самореферентный.",
        "category": "metadata",
    },
    "h1_missing": {
        "title": "Отсутствует заголовок H1",
        "description": "На странице нет основного заголовка, что снижает тематическую ясность для пользователей и поисковиков.",
        "recommendation": "Добавьте один чёткий H1, отражающий тему страницы.",
        "category": "content_structure",
    },
    "h1_too_short": {
        "title": "Заголовок H1 слишком короткий",
        "description": "Основной заголовок слишком лаконичен, чтобы передать тему страницы.",
        "recommendation": "Перепишите H1 так, чтобы он описывал тему страницы полнее и понятнее для пользователей.",
        "category": "content_structure",
    },
    "jsonld_missing": {
        "title": "Структурированные данные не найдены",
        "description": "На странице не обнаружена разметка JSON-LD.",
        "recommendation": "Добавьте разметку schema.org там, где это помогает поисковикам понять тип контента или сущности страницы.",
        "category": "structured_data",
    },
    "jsonld_empty": {
        "title": "Пустой блок структурированных данных",
        "description": "Тег JSON-LD присутствует, но не содержит полезных данных.",
        "recommendation": "Заполните блок структурированных данных корректным schema.org JSON-LD.",
        "category": "structured_data",
    },
    "jsonld_invalid_json": {
        "title": "Некорректный JSON в структурированных данных",
        "description": "Блок JSON-LD содержит синтаксическую ошибку JSON и не может быть разобран.",
        "recommendation": "Исправьте синтаксис JSON в блоке структурированных данных и проверьте его корректность перед публикацией.",
        "category": "structured_data",
    },
    "jsonld_invalid_structure": {
        "title": "Неподдерживаемая структура JSON-LD",
        "description": "Блок структурированных данных использует неожиданную структуру, которую невозможно надёжно интерпретировать.",
        "recommendation": "Убедитесь, что JSON-LD является объектом или массивом объектов с полями schema.org.",
        "category": "structured_data",
    },
    "jsonld_missing_context": {
        "title": "В JSON-LD отсутствует @context",
        "description": "В объекте JSON-LD нет @context, поэтому обработчики схем могут интерпретировать его некорректно.",
        "recommendation": "Добавьте \"@context\": \"https://schema.org\" в объект структурированных данных.",
        "category": "structured_data",
    },
    "jsonld_missing_type": {
        "title": "В JSON-LD отсутствует @type",
        "description": "В объекте JSON-LD нет явного типа, поэтому сущность, представленная разметкой, не определена.",
        "recommendation": "Добавьте подходящий @type из schema.org для страницы или сущности.",
        "category": "structured_data",
    },
    "broken_link_404": {
        "title": "Битая внутренняя ссылка",
        "description": "Внутренний URL вернул 404 при проверке, создавая тупик для crawler'ов и пользователей.",
        "recommendation": "Исправьте URL назначения, восстановите страницу или удалите битую внутреннюю ссылку.",
        "category": "links",
    },
    "link_check_blocked": {
        "title": "Проверка ссылки заблокирована",
        "description": "Сервер заблокировал проверку внутреннего URL, поэтому аудит не смог подтвердить его статус.",
        "recommendation": "Проверьте настройки rate-limiting, защиты от ботов или прав доступа, влияющих на crawler.",
        "category": "links",
    },
    "link_check_error": {
        "title": "Ошибка проверки ссылки",
        "description": "Crawler не смог проверить внутренний URL из-за ошибки запроса.",
        "recommendation": "Проверьте целевой эндпоинт и повторите попытку после устранения проблем с подключением или TLS.",
        "category": "links",
    },
    "cwv_lcp_poor": {
        "title": "Largest Contentful Paint слишком высокий",
        "description": "Основной контент становится видимым слишком медленно, что ухудшает воспринимаемую скорость загрузки.",
        "recommendation": "Оптимизируйте время ответа сервера, ресурсы, блокирующие рендеринг, и крупные элементы верхней части страницы.",
        "category": "performance",
    },
    "cwv_inp_poor": {
        "title": "Interaction to Next Paint слишком высокий",
        "description": "Страница медленно реагирует на действия пользователя (клики, касания, нажатия клавиш). INP заменил FID как Core Web Vital в марте 2024 года.",
        "recommendation": "Сократите длинные задачи в основном потоке, разбейте тяжёлые скрипты и перенесите некритичный JavaScript в конец загрузки.",
        "category": "performance",
    },
    "cwv_cls_poor": {
        "title": "Cumulative Layout Shift слишком высокий",
        "description": "Видимые элементы неожиданно смещаются во время загрузки, создавая неудобство для пользователей.",
        "recommendation": "Резервируйте пространство для медиа и динамических элементов и избегайте вставки контента выше сгиба страницы.",
        "category": "performance",
    },
    "cwv_unavailable": {
        "title": "Core Web Vitals не удалось получить",
        "description": "Аудит не смог загрузить данные PageSpeed для этого URL, поэтому оценка производительности является частично расчётной.",
        "recommendation": "Повторите аудит позже или настройте API-ключ PageSpeed для более высокой квоты и стабильного сбора данных.",
        "category": "performance",
    },
    "backlinks_unavailable": {
        "title": "Данные об обратных ссылках не получены",
        "description": "Аудит не смог получить данные о ссылках из настроенного внешнего источника.",
        "recommendation": "Проверьте учётные данные внешней интеграции и повторите полный аудит.",
        "category": "links",
    },
    "spa_detected_enable_js_render": {
        "title": "Обнаружен SPA-рендеринг",
        "description": "Страница, по всей видимости, использует клиентский рендеринг, но JavaScript-рендеринг для этого обхода был отключён.",
        "recommendation": "Повторите аудит с включённым JavaScript-рендерингом или добавьте серверный рендеринг/пре-рендеринг.",
        "category": "rendering",
    },
    "anti_bot_detected": {
        "title": "Обнаружена антиботовая защита",
        "description": "Целевой сайт ответил так, что это указывает на защиту от ботов или ограничение запросов.",
        "recommendation": "Снизьте агрессивность обхода, добавьте crawler в белый список или запустите аудит из разрешённой среды.",
        "category": "crawlability",
    },
    "page_timeout": {
        "title": "Превышено время ожидания страницы",
        "description": "Crawler не смог завершить запрос в течение установленного времени ожидания.",
        "recommendation": "Исследуйте задержки сервера и крупные блокирующие ресурсы на затронутой странице.",
        "category": "availability",
    },
    "page_fetch_error": {
        "title": "Ошибка загрузки страницы",
        "description": "Crawler столкнулся с транспортной ошибкой или ошибкой рендеринга при попытке загрузить страницу.",
        "recommendation": "Проверьте логи сервера, конфигурацию TLS и правила доступа для ботов на затронутом URL.",
        "category": "availability",
    },
    "page_server_error": {
        "title": "Страница вернула ошибку сервера",
        "description": "Страница ответила кодом статуса 5xx, что препятствует надёжному обходу и индексированию.",
        "recommendation": "Устраните серверную ошибку на затронутом URL и повторите аудит.",
        "category": "availability",
    },
}


SEVERITY_WEIGHTS = {
    "critical": 35.0,
    "high": 22.0,
    "medium": 10.0,
    "low": 4.0,
    "info": 0.0,
}
SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
CONFIDENCE_RANK = {"high": 0, "medium": 1, "low": 2}
NON_PROBLEM_CODES = {"audit_score_explanation"}

AUDIT_CRITERIA = [
    {
        "key": "technical_access",
        "title": "Технический доступ и индексируемость",
        "description": "Проверяет, доступен ли сайт для безопасного обхода и индексации без очевидных блокировок.",
        "categories": {"precheck", "availability", "crawlability", "rendering"},
        "weight": 0.24,
        "checked": [
            "Публичность URL и безопасность хоста",
            "Доступность robots.txt",
            "Наличие sitemap",
            "Ответ сервера и доступность для краулера",
            "Риски клиентского рендеринга",
        ],
    },
    {
        "key": "metadata",
        "title": "Поисковые метаданные",
        "description": "Проверяет теги title и meta description, формирующие первое впечатление в поисковой выдаче.",
        "categories": {"metadata"},
        "weight": 0.18,
        "checked": [
            "Наличие тега title",
            "Длина title",
            "Наличие meta description",
            "Длина meta description",
            "Тег canonical",
        ],
    },
    {
        "key": "content_structure",
        "title": "Структура контента",
        "description": "Проверяет наличие основного заголовка и базовой тематической структуры страницы.",
        "categories": {"content_structure"},
        "weight": 0.14,
        "checked": [
            "Наличие H1",
            "Ясность H1",
            "Базовая структура страницы",
        ],
    },
    {
        "key": "structured_data",
        "title": "Структурированные данные",
        "description": "Проверяет JSON-LD разметку и поля, необходимые поисковым системам для её интерпретации.",
        "categories": {"structured_data"},
        "weight": 0.14,
        "checked": [
            "Наличие JSON-LD",
            "Корректный JSON синтаксис",
            "schema.org @context",
            "Schema @type",
        ],
    },
    {
        "key": "internal_links",
        "title": "Внутренние ссылки",
        "description": "Проверяет внутренние URL на наличие битых адресов и проблем с проверкой.",
        "categories": {"links"},
        "weight": 0.12,
        "checked": [
            "Доступность внутренних ссылок",
            "Обнаружение 404",
            "Доступность для проверки ссылок",
        ],
    },
    {
        "key": "performance",
        "title": "Core Web Vitals",
        "description": "Проверяет доступные сигналы скорости загрузки и качества взаимодействия из данных PageSpeed.",
        "categories": {"performance"},
        "weight": 0.18,
        "checked": [
            "Largest Contentful Paint",
            "Interaction to Next Paint",
            "Cumulative Layout Shift",
            "Доступность PageSpeed",
        ],
    },
]


def _criterion_status(score: int, problem_count: int, info_count: int) -> str:
    if problem_count == 0 and info_count == 0:
        return "passed"
    if problem_count == 0:
        return "info"
    if score < 50:
        return "failed"
    if score < 80:
        return "warning"
    return "passed_with_notes"


def _build_criteria_results(findings: list[dict]) -> list[dict]:
    category_to_criterion = {}
    for criterion in AUDIT_CRITERIA:
        for category in criterion["categories"]:
            category_to_criterion[category] = criterion["key"]

    grouped: dict[str, list[dict]] = {criterion["key"]: [] for criterion in AUDIT_CRITERIA}
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        code = str(finding.get("code") or "")
        if code in NON_PROBLEM_CODES:
            continue
        category = str(finding.get("category") or "technical")
        criterion_key = category_to_criterion.get(category, "technical_access")
        grouped.setdefault(criterion_key, []).append(finding)

    results = []
    for criterion in AUDIT_CRITERIA:
        criterion_findings = grouped.get(criterion["key"], [])
        problem_findings = [
            finding
            for finding in criterion_findings
            if str(finding.get("severity") or "info").lower() != "info"
        ]
        info_findings = [
            finding
            for finding in criterion_findings
            if str(finding.get("severity") or "info").lower() == "info"
        ]
        penalty = sum(
            SEVERITY_WEIGHTS.get(str(finding.get("severity") or "info").lower(), 0.0)
            for finding in problem_findings
        )
        score = int(round(max(0.0, min(100.0, 100.0 - penalty))))
        results.append(
            {
                "key": criterion["key"],
                "title": criterion["title"],
                "description": criterion["description"],
                "score": score,
                "status": _criterion_status(score, len(problem_findings), len(info_findings)),
                "weight": criterion["weight"],
                "checked": criterion["checked"],
                "issue_count": len(problem_findings),
                "info_count": len(info_findings),
                "findings": criterion_findings,
            }
        )

    return results


def _is_private_ip(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:
        return False
    for family, _, _, _, sockaddr in infos:
        ip = sockaddr[0]
        try:
            addr = ipaddress.ip_address(ip)
            if addr.is_private or addr.is_loopback or addr.is_link_local:
                return True
        except ValueError:
            continue
    return False


def _precheck_url(root_url: str) -> list[dict]:
    findings = []
    parsed = urlparse(root_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        findings.append({"code": "invalid_url", "severity": "high", "confidence": "high", "details": {"root_url": root_url}})
        return findings
    if _is_private_ip(parsed.hostname or ""):
        findings.append({"code": "unsafe_target", "severity": "high", "confidence": "high", "details": {"host": parsed.hostname}})
    return findings


def _issue_key(finding: dict) -> tuple[str, str]:
    details = finding.get("details") or {}
    return (
        str(finding.get("code") or ""),
        str(details.get("url") or details.get("host") or details.get("root_url") or ""),
    )


def _decorate_finding(finding: dict) -> dict:
    decorated = dict(finding)
    code = str(decorated.get("code") or "")
    catalog_item = FINDING_CATALOG.get(code, {})
    details = decorated.get("details") or {}
    target = details.get("url") or details.get("root_url") or details.get("host") or ""

    if catalog_item:
        decorated.setdefault("title", catalog_item["title"])
        decorated.setdefault("description", catalog_item["description"])
        decorated.setdefault("recommendation", catalog_item["recommendation"])
        decorated.setdefault("category", catalog_item["category"])
    else:
        decorated.setdefault("title", code.replace("_", " ").title())
        decorated.setdefault("description", code.replace("_", " "))
        decorated.setdefault("recommendation", "Изучите затронутую страницу и устраните проблему перед повторным запуском аудита.")
        decorated.setdefault("category", "technical")

    if target:
        decorated["description"] = f"{decorated['description']} Затронутый объект: {target}."

    return decorated


def _build_page_level_findings(pages: list[dict]) -> list[dict]:
    findings: list[dict] = []
    for page in pages:
        url = page.get("url")
        error = page.get("error")
        status_code = page.get("status_code")

        if error == "timeout":
            findings.append({
                "code": "page_timeout",
                "severity": "high",
                "confidence": "high",
                "details": {"url": url},
            })
        elif error:
            findings.append({
                "code": "page_fetch_error",
                "severity": "high",
                "confidence": "medium",
                "details": {"url": url, "error": error},
            })

        if isinstance(status_code, int) and 500 <= status_code <= 599:
            findings.append({
                "code": "page_server_error",
                "severity": "high",
                "confidence": "high",
                "details": {"url": url, "status_code": status_code},
            })
    return findings


def _compute_score(summary: dict, findings: list[dict]) -> dict:
    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
    for finding in findings:
        severity = str(finding.get("severity") or "info").lower()
        if severity not in counts:
            severity = "info"
        counts[severity] += 1

    coverage = summary.get("coverage") or {}
    processed_pages = int(coverage.get("processed") or 0)
    attempted_pages = int(coverage.get("attempted") or 0)
    max_pages = max(1, int(coverage.get("max_pages") or 1))
    crawl_completion = min(1.0, processed_pages / max(1, attempted_pages or processed_pages or max_pages))
    criteria = _build_criteria_results(findings)
    weighted_score = sum(item["score"] * float(item["weight"]) for item in criteria)
    total_weight = sum(float(item["weight"]) for item in criteria) or 1.0
    coverage_penalty = 0.0
    if attempted_pages > 0 and processed_pages < attempted_pages:
        coverage_penalty = min(15.0, (1.0 - crawl_completion) * 15.0)
    elif processed_pages == 0 and not summary.get("precheck_failed"):
        coverage_penalty = 20.0

    score = round(max(0.0, min(100.0, (weighted_score / total_weight) - coverage_penalty)))
    penalty = 100.0 - score

    return {
        "score": int(score),
        "criteria": criteria,
        "issue_counts": counts,
        "score_breakdown": {
            "base_score": 100,
            "penalty_points": round(penalty, 2),
            "coverage_bonus": 0,
            "crawl_bonus": 0,
            "coverage_penalty": round(coverage_penalty, 2),
            "crawl_completion_ratio": round(crawl_completion, 2),
        },
        "score_explanation": (
            f"Оценка представляет собой взвешенное среднее стандартных критериев аудита. Обработано страниц: {processed_pages}; "
            f"критических проблем: {counts['critical']}; высоких: {counts['high']}; "
            f"средних: {counts['medium']}; низких: {counts['low']}."
        ),
    }


def select_top_findings(findings: list, limit: int = 5) -> list[dict]:
    if limit <= 0:
        return []

    ranked: list[tuple[int, int, int, dict]] = []
    for index, finding in enumerate(findings):
        if not isinstance(finding, dict):
            continue

        code = str(finding.get("code") or "")
        severity = str(finding.get("severity") or "info").lower()
        if code in NON_PROBLEM_CODES or severity == "info":
            continue

        confidence = str(finding.get("confidence") or "medium").lower()
        ranked.append(
            (
                SEVERITY_RANK.get(severity, SEVERITY_RANK["info"]),
                CONFIDENCE_RANK.get(confidence, CONFIDENCE_RANK["medium"]),
                index,
                dict(finding),
            )
        )

    ranked.sort(key=lambda item: item[:3])
    return [finding for _, _, _, finding in ranked[:limit]]


def _build_score_explanation_finding(summary: dict) -> dict:
    breakdown = summary.get("score_breakdown") or {}
    counts = summary.get("issue_counts") or {}
    coverage = summary.get("coverage") or {}
    cwv = summary.get("cwv") or {}
    cwv_text = "Core Web Vitals недоступны"
    if cwv:
        cwv_text = (
            f"LCP: {cwv.get('LCP_grade', 'unknown')}, "
            f"INP: {cwv.get('INP_grade', 'unknown')}, "
            f"CLS: {cwv.get('CLS_grade', 'unknown')}"
        )

    return {
        "code": "audit_score_explanation",
        "severity": "info",
        "confidence": "high",
        "category": "scoring",
        "title": "Как рассчитана оценка аудита",
        "description": (
            "Итоговая оценка — взвешенное среднее стандартных критериев: индексируемость, метаданные, структура контента, "
            "структурированные данные, внутренние ссылки и Core Web Vitals. "
            f"Обработано страниц: {coverage.get('processed', 0)} из {coverage.get('max_pages', 0)}. "
            f"Количество проблем — критических: {counts.get('critical', 0)}, высоких: {counts.get('high', 0)}, "
            f"средних: {counts.get('medium', 0)}, низких: {counts.get('low', 0)}. "
            f"Суммарное штрафное влияние: {breakdown.get('penalty_points', 0)}. Core Web Vitals: {cwv_text}."
        ),
        "recommendation": "Сначала устраните проблемы высокой критичности, затем предупреждения в метаданных и структурированных данных, после чего повторите аудит.",
    }


async def _load_audit_row(audit_id: str, row_cls: Type[PublicAuditResult] | Type[CrawlResult]) -> PublicAuditResult | CrawlResult:
    async with get_session() as session:
        result = await session.execute(select(row_cls).where(row_cls.audit_id == audit_id))
        row = result.scalar_one_or_none()
    if row is None:
        raise ValueError("audit_not_found")
    return row


async def _run_audit_pipeline(
    audit_id: str,
    *,
    row_cls: Type[PublicAuditResult] | Type[CrawlResult],
    include_external_data: bool,
) -> dict:
    row = await _load_audit_row(audit_id, row_cls)
    root_url = row.root_url
    options = row.options or {}

    findings = []
    precheck_findings = _precheck_url(root_url)
    findings.extend(precheck_findings)
    if any(finding["code"] in ("invalid_url", "unsafe_target") for finding in precheck_findings):
        summary = {
            "coverage": {"attempted": 0, "processed": 0, "max_pages": int(options.get("max_pages", 10))},
            "precheck_failed": True,
        }
        findings = [_decorate_finding(finding) for finding in findings]
        summary.update(_compute_score(summary, findings))
        findings.append(_build_score_explanation_finding(summary))
        return {"root_url": root_url, "summary": summary, "findings": findings, "top_findings": select_top_findings(findings), "pages": []}

    max_pages = int(options.get("max_pages", 10 if row.mode == "public" else 1000))
    max_depth = int(options.get("max_depth", 2 if row.mode == "public" else 4))
    if row.mode == "public":
        max_depth = min(max_depth, 2)
    js_render = bool(options.get("js_render", False))
    respect_robots = bool(options.get("respect_robots", True))
    timeout = float(options.get("timeout", settings.default_timeout_s))

    robots = await check_robots(root_url=root_url)
    sitemap = await check_sitemap(root_url=root_url)
    summary = {"robots": robots, "sitemap": sitemap}

    if not sitemap.get("available"):
        findings.append({"code": "sitemap_missing", "severity": "medium", "confidence": "high", "details": sitemap})

    if respect_robots and robots.get("blocked_root", False):
        findings.append({"code": "blocked_by_robots", "severity": "medium", "confidence": "high", "details": robots})
        summary.update({"coverage": {"attempted": 0, "processed": 0, "max_pages": max_pages, "max_depth": max_depth}, "blocked_pages_count": 0, "precheck_failed": False})
        findings = [_decorate_finding(finding) for finding in findings]
        summary.update(_compute_score(summary, findings))
        findings.append(_build_score_explanation_finding(summary))
        return {"root_url": root_url, "summary": summary, "findings": findings, "top_findings": select_top_findings(findings), "pages": []}

    crawled = await crawl_public(
        root_url=root_url,
        max_pages=max_pages,
        max_depth=max_depth,
        js_render=js_render,
        timeout_s=timeout,
        respect_robots=respect_robots,
    )
    pages = crawled["pages"]
    summary.update(crawled["summary"])
    findings.extend(_build_page_level_findings(pages))

    for page in pages:
        findings.extend(check_meta(page.get("url"), page.get("title"), page.get("description"), page.get("h1")))

        html = page.get("html")
        if html:
            findings.extend(check_canonical(page.get("url"), html))
            findings.extend(validate_jsonld(page.get("url"), html))

    link_findings, links_checked = await check_links_404(root_url=root_url, pages=pages)
    findings.extend(link_findings)
    summary["links_checked"] = links_checked

    cwv = await analyze_cwv(root_url=root_url)
    if cwv:
        summary["cwv"] = cwv.get("summary", {})
        findings.extend(cwv.get("findings", []))
    else:
        findings.append({
            "code": "cwv_unavailable",
            "severity": "info",
            "confidence": "high",
            "details": {"reason": "pagespeed_unavailable_or_quota_limited"},
        })

    if include_external_data:
        try:
            from services.audit_service.integrations.gsc_link_analyzer import analyze_links

            backlink_summary = await asyncio.to_thread(analyze_links, row.project_id, root_url)
            summary["backlinks"] = backlink_summary
        except Exception as exc:
            findings.append({
                "code": "backlinks_unavailable",
                "severity": "info",
                "confidence": "medium",
                "details": {"reason": str(exc)},
            })

    if summary.get("spa_detected") and not js_render:
        findings.append({
            "code": "spa_detected_enable_js_render",
            "severity": "medium",
            "confidence": "medium",
            "details": {"root_url": root_url},
        })

    if summary.get("anti_bot_detected"):
        findings.append({
            "code": "anti_bot_detected",
            "severity": "medium",
            "confidence": "medium",
            "details": {"hint": "reduce_concurrency_or_use_js_render"},
        })

    deduped = {}
    for finding in findings:
        deduped[_issue_key(finding)] = finding

    findings = [_decorate_finding(finding) for finding in deduped.values()]
    summary["mode"] = row.mode
    if row.project_id:
        summary["project_id"] = row.project_id
    summary.update(_compute_score(summary, findings))
    findings.append(_build_score_explanation_finding(summary))

    return {
        "project_id": row.project_id,
        "mode": row.mode,
        "root_url": root_url,
        "summary": summary,
        "findings": findings,
        "top_findings": select_top_findings(findings),
        "pages": pages,
    }


async def run_public_audit_pipeline(audit_id: str) -> dict:
    return await _run_audit_pipeline(
        audit_id,
        row_cls=PublicAuditResult,
        include_external_data=False,
    )


async def run_full_audit_pipeline(audit_id: str) -> dict:
    return await _run_audit_pipeline(
        audit_id,
        row_cls=CrawlResult,
        include_external_data=True,
    )

