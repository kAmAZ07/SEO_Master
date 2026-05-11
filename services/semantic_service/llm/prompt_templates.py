TITLE_PROMPT = """You are an SEO assistant. Generate a concise, high-quality page <title> for the page.
Constraints:
- 45-65 characters recommended
- No clickbait
- Must reflect the content accurately
Return only the title text."""

DESCRIPTION_PROMPT = """You are an SEO assistant. Generate a meta description.
Constraints:
- 120-160 characters recommended
- Clear value proposition
- Must reflect the content accurately
Return only the description text."""

H1_PROMPT = """You are an SEO assistant. Generate a single H1 heading.
Constraints:
- Clear, descriptive, not too long
- Must match the page intent
Return only the H1 text."""

SCHEMA_PROMPT = """You are an SEO assistant. Produce a minimal valid JSON-LD Schema.org snippet for this page.
Constraints:
- Must be valid JSON
- Use appropriate @type (WebPage, Article, Product, FAQPage if clearly applicable)
Return only JSON (no markdown fences)."""  