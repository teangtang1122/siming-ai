"""Tests for contracts introduced by the 3.0 architecture foundation."""
from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import patch

from pydantic import BaseModel
from sqlalchemy import Column, Integer, create_engine, select
from sqlalchemy.orm import declarative_base, sessionmaker

from app.architecture.contracts import (
    AttentionRequired,
    ModelMessage,
    ModelRequest,
    OperationResult,
)
from app.architecture.tool_spec import ToolSpec
from app.architecture.uow import SqlAlchemyUnitOfWork
from app.modules import MODULES

ArchitectureBase = declarative_base()


class Row(ArchitectureBase):
    __tablename__ = "architecture_test_rows"

    id = Column(Integer, primary_key=True)


class ExampleInput(BaseModel):
    value: int


class ExampleOutput(BaseModel):
    doubled: int


def _session_factory():
    engine = create_engine("sqlite:///:memory:")
    ArchitectureBase.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine)


def test_unit_of_work_commits_only_when_explicit():
    engine, Session = _session_factory()
    with SqlAlchemyUnitOfWork(Session) as uow:
        uow.session.add(Row(id=1))
    with Session() as session:
        assert session.scalar(select(Row.id)) is None

    with SqlAlchemyUnitOfWork(Session) as uow:
        uow.session.add(Row(id=2))
        uow.commit()
    with Session() as session:
        assert session.scalar(select(Row.id)) == 2
    engine.dispose()


def test_request_bound_unit_of_work_rolls_back_without_closing_owner_session():
    engine, Session = _session_factory()
    with Session() as session:
        with patch.object(session, "close", wraps=session.close) as close:
            with SqlAlchemyUnitOfWork.from_session(session) as uow:
                uow.session.add(Row(id=3))
            close.assert_not_called()
        assert session.scalar(select(Row.id).where(Row.id == 3)) is None
    engine.dispose()


def test_tool_spec_generates_and_validates_json_schema():
    spec = ToolSpec(
        name="double_value",
        description="Double an integer.",
        input_model=ExampleInput,
        output_model=ExampleOutput,
    )
    assert spec.validate_input({"value": 3}).value == 3
    schema = spec.openai_schema()
    assert schema["function"]["name"] == "double_value"
    assert "value" in schema["function"]["parameters"]["properties"]
    assert "doubled" in spec.output_schema()["properties"]


def test_shared_operation_contract_has_one_attention_shape():
    result = OperationResult(
        outcome="waiting_user",
        summary="World style needs confirmation.",
        attention=AttentionRequired(
            kind="confirmation",
            title="Confirm world style",
            message="Review the generated stage before continuing.",
        ),
    )
    assert result.attention is not None
    assert result.attention.blocking is True


def test_model_request_never_serializes_null_max_tokens_as_a_number():
    request = ModelRequest(
        messages=[ModelMessage(role="user", content="Hello")],
        max_tokens=None,
    )
    payload = request.model_dump(exclude_none=True)
    assert "max_tokens" not in payload


def test_module_manifest_has_unique_names():
    names = [module.name for module in MODULES]
    assert len(names) == len(set(names)) == 8


def test_prompt_tool_vocabulary_matches_legacy_registry():
    from app.prompts.workspace_contract import WORKSPACE_TOOL_NAMES
    from app.services.workspace.tool_schemas import SEARCH_TOOL_NAMES, WRITE_TOOL_NAMES

    assert WORKSPACE_TOOL_NAMES == SEARCH_TOOL_NAMES | WRITE_TOOL_NAMES


def test_http_routers_do_not_access_sqlalchemy_models_directly():
    router_root = Path(__file__).resolve().parents[1] / "app" / "routers"
    violations: list[str] = []
    for path in sorted(router_root.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module.endswith("database.models"):
                    violations.append(f"{path.name}:{node.lineno}: ORM import")
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "query"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "db"
            ):
                violations.append(f"{path.name}:{node.lineno}: db.query")
    assert violations == []
