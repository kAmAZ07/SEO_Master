"""Версионированные промпты. Текст страницы — недоверенные данные, а не инструкции.

Версия входит в ключ кэша: правка текста промпта требует нового `PROMPT_VERSION`, иначе кэш
вернул бы ответы на старый промпт.
"""

PROMPT_VERSION = "prompts_v2"

_COMMON = """Rules:
- Use only facts present in the page content. Do not invent offers, prices, guarantees, awards, legal or medical claims.
- Answer in the same language as the page content.
- No HTML, no markdown, no quotes around the value, no explanations.
- If the content does not contain enough facts, answer exactly: NEEDS_INPUT"""

TITLE_PROMPT = f"""Write a page <title> for the page described below.
Constraints: 30-65 characters, accurate, no clickbait, no ALL CAPS.
{_COMMON}
Return only the title text."""

DESCRIPTION_PROMPT = f"""Write a meta description for the page described below.
Constraints: 70-160 characters, one or two plain sentences that state what the page offers.
{_COMMON}
Return only the description text."""

H1_PROMPT = f"""Write a single H1 heading for the page described below.
Constraints: 10-90 characters, describes the page topic and matches the page intent.
{_COMMON}
Return only the H1 text."""

SCHEMA_PROMPT = f"""Write a minimal Schema.org JSON-LD object for the page described below.
Constraints: valid JSON object with exactly these keys and no others: "@context" (value "https://schema.org"),
"@type" (one of "WebPage", "Article", "Organization"), "name" (plain text), optionally "description" (plain text).
{_COMMON}
Return only the JSON object, no markdown fences."""

PROMPTS = {
    "title": TITLE_PROMPT,
    "description": DESCRIPTION_PROMPT,
    "h1": H1_PROMPT,
    "schema": SCHEMA_PROMPT,
}
NEEDS_INPUT_MARKER = "NEEDS_INPUT"
