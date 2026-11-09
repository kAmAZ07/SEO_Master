"""Приём результатов AL-5 без runtime-импортов Semantic и подстановки баллов."""

from dataclasses import dataclass
import math
from typing import Literal

from pydantic import ValidationError

from services.semantic_service.schemas.ff_score import FFScoreResponse


ScoreStatus = Literal["complete", "insufficient_data"]


class ScoreContractError(ValueError):
    """Безопасная диагностика; внешнее тело и ошибки валидации не раскрываются."""

    def __init__(self) -> None:
        super().__init__("semantic_score_contract_invalid")


def score_status(value: object, status: object) -> ScoreStatus:
    """Полный балл: конечное число 0..100 либо явный null при недостатке данных."""
    if status not in ("complete", "insufficient_data"):
        raise ScoreContractError()
    if status == "insufficient_data":
        if value is not None:
            raise ScoreContractError()
        return "insufficient_data"
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not 0 <= value <= 100
        or not math.isfinite(value)
    ):
        raise ScoreContractError()
    return "complete"


def parse_score_response(payload: object) -> FFScoreResponse:
    """Использует опубликованную HTTP-схему AL-5, не создаёт её вторую копию."""
    try:
        result = FFScoreResponse.model_validate(payload, strict=True)
    except ValidationError:
        raise ScoreContractError() from None
    score_status(result.ff_score, result.status)
    details = result.eeat
    if (
        result.formula_version != "ff_score_v1"
        or details.get("formula_version") != "eeat_v1"
        or not result.ff_score_id
        or not result.eeat_score_id
        or details.get("score_id") != result.eeat_score_id
        or "score" not in details
    ):
        raise ScoreContractError()
    score_status(details["score"], details.get("status"))
    return result


@dataclass(frozen=True)
class ScoreObservation:
    """Только проекция нужных Management полей, не новая схема события."""

    project_id: str
    ff_score: float | None
    status: ScoreStatus
    formula_version: str
    eeat_score: float | None
    eeat_present: bool
    eeat_status: ScoreStatus | None


def _number(value: object) -> float | None:
    score_status(value, "insufficient_data" if value is None else "complete")
    # score_status выше исключает bool и прочие типы до преобразования.
    if isinstance(value, (int, float)):
        return float(value)
    return None


def parse_score_event(event: object) -> ScoreObservation:
    """Проверяет используемые поля v2 и сохраняет явную legacy-совместимость.

    Отсутствующий schema_version означает старый payload, а не подтверждённый
    AL-5 результат. Явный null никогда не заменяется значением другого alias.
    """
    if not isinstance(event, dict):
        raise ScoreContractError()
    payload = event.get("payload", event)
    if not isinstance(payload, dict):
        raise ScoreContractError()
    project_id = payload.get("project_id")
    if not isinstance(project_id, str) or not project_id.strip():
        raise ScoreContractError()
    schema = payload.get("schema_version")
    if "schema_version" in payload and schema != "ffscore_recalculated/2":
        raise ScoreContractError()
    is_v2 = schema == "ffscore_recalculated/2"
    if "ff_score" not in payload and (is_v2 or "ffscore" not in payload):
        raise ScoreContractError()
    ff_score = _number(payload["ff_score"] if "ff_score" in payload else payload["ffscore"])
    if "ffscore" in payload and _number(payload["ffscore"]) != ff_score:
        raise ScoreContractError()
    eeat_present = "eeat_score" in payload or "eeat" in payload
    if is_v2 and ("eeat_score" not in payload or "eeat" not in payload):
        raise ScoreContractError()
    eeat_score = _number(payload.get("eeat_score") if "eeat_score" in payload else payload.get("eeat"))
    if "eeat" in payload and _number(payload["eeat"]) != eeat_score:
        raise ScoreContractError()
    status = score_status(ff_score, payload.get("status") if is_v2 else (
        "insufficient_data" if ff_score is None else "complete"
    ))
    formula = payload.get("formula_version") if is_v2 else "legacy"
    if not isinstance(formula, str) or (is_v2 and formula != "ff_score_v1"):
        raise ScoreContractError()
    eeat_status: ScoreStatus | None = None
    if eeat_present:
        eeat_status = "insufficient_data" if eeat_score is None else "complete"
    if is_v2:
        details = payload.get("eeat_details")
        if not isinstance(details, dict) or "score" not in details:
            raise ScoreContractError()
        if _number(details["score"]) != eeat_score or details.get("formula_version") != "eeat_v1":
            raise ScoreContractError()
        score_status(eeat_score, details.get("status"))
    return ScoreObservation(project_id, ff_score, status, formula, eeat_score, eeat_present, eeat_status)
