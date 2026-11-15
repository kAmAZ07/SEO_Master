"""Compatibility entry points: Tilda-native writes have no validated contract."""
from shared.tilda_contract import unsupported


class TildaMetaInjector:
    def __init__(self, client: object = None) -> None:
        self.client = client

    async def apply_fields(self, page_id: str, fields: object) -> dict[str, object]:
        return unsupported(page_id)

    async def apply_meta(self, page_id: str, meta: object) -> dict[str, object]:
        return unsupported(page_id)

    async def apply_schema(self, page_id: str, schema_value: object) -> dict[str, object]:
        return unsupported(page_id)

    async def apply_interlinks(self, page_id: str, links: object) -> dict[str, object]:
        return unsupported(page_id)
