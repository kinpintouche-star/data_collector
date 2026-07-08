from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.exc import IntegrityError

from ict.db.repositories import StrategyDefinitionRepository, json_safe
from ict.db.session import session_scope
from ict.strategy.builder import (
    StrategyDefinitionCreate,
    StrategyDefinitionPayload,
    StrategyDefinitionPublic,
    StrategyDefinitionUpdate,
    catalog_payload,
    definition_hash,
    export_strategy_yaml,
    template_by_id,
    validation_result,
)


def get_strategy_builder_catalog() -> dict[str, Any]:
    return catalog_payload()


def list_strategy_definitions() -> list[dict[str, Any]]:
    with session_scope() as session:
        rows = StrategyDefinitionRepository(session).list()
        return [_public(row).model_dump(mode="json") for row in rows]


def create_strategy_definition(payload: StrategyDefinitionCreate) -> dict[str, Any]:
    definition = _definition_from_create(payload)
    result = validation_result(definition)
    if not result.valid:
        raise ValueError("; ".join(result.errors))
    with session_scope() as session:
        try:
            row = StrategyDefinitionRepository(session).create(
                name=payload.name,
                version=payload.version,
                description=payload.description,
                definition=definition.model_dump(mode="json"),
                definition_hash=result.definition_hash or definition_hash(definition.model_dump(mode="json")),
            )
            return _public(row).model_dump(mode="json")
        except IntegrityError as exc:
            raise ValueError(f"Strategy definition already exists: {payload.name} {payload.version}") from exc


def get_strategy_definition(strategy_id: str) -> dict[str, Any]:
    with session_scope() as session:
        row = StrategyDefinitionRepository(session).require(uuid.UUID(strategy_id))
        return _public(row).model_dump(mode="json")


def update_strategy_definition(strategy_id: str, payload: StrategyDefinitionUpdate) -> dict[str, Any]:
    with session_scope() as session:
        repo = StrategyDefinitionRepository(session)
        row = repo.require(uuid.UUID(strategy_id))
        definition = payload.definition or StrategyDefinitionPayload.model_validate(row.definition)
        result = validation_result(definition)
        if not result.valid:
            raise ValueError("; ".join(result.errors))
        row = repo.update(
            row,
            name=payload.name,
            version=payload.version,
            description=payload.description,
            definition=definition.model_dump(mode="json") if payload.definition is not None else None,
            definition_hash=result.definition_hash,
        )
        return _public(row).model_dump(mode="json")


def validate_strategy_definition_record(strategy_id: str) -> dict[str, Any]:
    with session_scope() as session:
        repo = StrategyDefinitionRepository(session)
        row = repo.require(uuid.UUID(strategy_id))
        result = validation_result(row.definition)
        if result.valid:
            row.definition_hash = result.definition_hash or row.definition_hash
            row.status = "validated"
        return result.model_dump(mode="json")


def export_strategy_definition_record(strategy_id: str) -> dict[str, Any]:
    with session_scope() as session:
        repo = StrategyDefinitionRepository(session)
        row = repo.require(uuid.UUID(strategy_id))
        result = validation_result(row.definition)
        if not result.valid:
            raise ValueError("; ".join(result.errors))
        exported_path = export_strategy_yaml(row.name, row.version, row.definition)
        repo.set_exported_path(row, exported_path)
        return {"exported_path": exported_path, "strategy": _public(row).model_dump(mode="json")}


def delete_strategy_definition_record(strategy_id: str) -> dict[str, str]:
    with session_scope() as session:
        repo = StrategyDefinitionRepository(session)
        row = repo.require(uuid.UUID(strategy_id))
        deleted_id = str(row.id)
        repo.delete(row)
        return {"deleted_id": deleted_id}


def _definition_from_create(payload: StrategyDefinitionCreate) -> StrategyDefinitionPayload:
    if payload.definition is not None:
        return payload.definition
    if payload.template_id:
        template = template_by_id(payload.template_id)
        return StrategyDefinitionPayload.model_validate(template["definition"])
    raise ValueError("Use definition or template_id to create a strategy definition.")


def _public(row) -> StrategyDefinitionPublic:
    return StrategyDefinitionPublic(
        id=row.id,
        name=row.name,
        version=row.version,
        status=row.status,
        description=row.description,
        definition=json_safe(row.definition),
        definition_hash=row.definition_hash,
        exported_path=row.exported_path,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
