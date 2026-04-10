from services.audit_service.events.crawl_completed import publish_crawl_completed


async def publish_public_audit_completed(
    audit_id: str,
    root_url: str,
    summary: dict,
    *,
    pages: list[dict] | None = None,
    content_text: str = "",
) -> None:
    await publish_crawl_completed(
        audit_id=audit_id,
        project_id=None,
        root_url=root_url,
        mode="public",
        summary=summary,
        pages=pages or [],
        content_text=content_text,
    )
