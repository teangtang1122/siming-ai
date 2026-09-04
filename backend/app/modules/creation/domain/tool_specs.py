"""Typed contracts for new-novel workspace tools."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ....architecture.tool_spec import ToolSpec, project_typed_tool_spec


class CompatibleInput(BaseModel):
    model_config = ConfigDict(extra="allow", protected_namespaces=())


_CREATION_MODEL_DESCRIPTION = (
    "Optional model identity. When omitted, creation uses the active default model."
)

_PATCH_CHANGES_DESCRIPTION = (
    "原生 JSON 操作数组，不是 JSON 编码字符串。每个元素是包含 path、action 和 value 的对象；"
    "完整阶段使用 [{\"path\":\"/\",\"action\":\"set\",\"value\":{...}}]，"
    "value 中的对象和数组也必须直接传入，不要转成字符串。"
)


class StartNovelCreationSessionInput(CompatibleInput):
    mode: Literal["internal_llm", "external_agent"] = "external_agent"
    user_brief: str = ""
    target_audience: str = ""
    genre: str = ""
    platform: str = ""


class CreationSessionInput(CompatibleInput):
    session_id: str


class GetCreationOperationInput(CompatibleInput):
    operation_id: str = ""
    run_id: str = ""


class PatchCreationSessionInput(CompatibleInput):
    session_id: str
    expected_revision: int
    changes: dict[str, Any]


class CreationArtifactInput(CompatibleInput):
    session_id: str
    artifact: str


class CreationPatchOperation(CompatibleInput):
    model_config = ConfigDict(
        json_schema_extra={
            "oneOf": [
                {
                    "required": ["action"],
                    "properties": {
                        "action": {"enum": ["set", "replace", "append", "remove", "resize"]},
                        "op": {"type": "null"},
                    },
                },
                {
                    "required": ["op"],
                    "properties": {
                        "op": {"enum": ["add", "replace", "remove"]},
                        "action": {"type": "null"},
                    },
                },
            ],
            "allOf": [
                {
                    "if": {
                        "required": ["action"],
                        "properties": {"action": {"const": "resize"}},
                    },
                    "then": {
                        "required": ["target_count"],
                        "properties": {"target_count": {"type": "integer", "minimum": 0}},
                    },
                }
            ],
        }
    )
    action: Literal["set", "replace", "append", "remove", "resize"] | None = Field(
        default=None,
        description=(
            "司命 Patch 动作。向数组末尾增加元素时使用 append，并把 path 指向数组本身；"
            "也接受标准 JSON Patch 的 op 字段。"
        ),
    )
    op: Literal["add", "replace", "remove"] | None = Field(
        default=None,
        description=(
            "兼容标准 JSON Patch。add 到 /- 会自动转换为 append；"
            "add 到对象字段会转换为 set。action 与 op 二选一。"
        ),
    )
    path: str = Field(
        description="目标 JSON Pointer，例如 /special_requirements 或 /volumes/0/title"
    )
    value: Any = Field(default=None, description="set、replace、append 或 add 写入的值")
    target_count: int | None = Field(default=None, ge=0, description="resize 的目标数组长度")
    fill_value: Any = Field(default=None, description="resize 扩展数组时使用的填充值")

    @model_validator(mode="after")
    def require_one_operation_form(self) -> CreationPatchOperation:
        if (self.action is None) == (self.op is None):
            raise ValueError("action 与 op 必须且只能提供一个")
        if self.action == "resize" and self.target_count is None:
            raise ValueError("resize 操作必须提供 target_count")
        return self


class ListCreationArtifactsInput(CompatibleInput):
    session_id: str


class PatchCreationArtifactInput(CreationArtifactInput):
    expected_revision: int
    changes: list[CreationPatchOperation] = Field(
        min_length=1,
        description=_PATCH_CHANGES_DESCRIPTION,
    )


class CreationArtifactLockInput(CreationArtifactInput):
    expected_revision: int
    paths: list[str]


class UndoCreationArtifactInput(CreationArtifactInput):
    expected_revision: int


class ListCreationEntitiesInput(CompatibleInput):
    session_id: str
    artifact: str = ""
    entity_type: str = ""
    include_deleted: bool = False
    query: str = ""
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=50)


class CreationEntityInput(CompatibleInput):
    entity_id: str


class PatchCreationEntityInput(CreationEntityInput):
    expected_revision: int
    changes: list[CreationPatchOperation] = Field(
        min_length=1,
        description=_PATCH_CHANGES_DESCRIPTION,
    )


class DeleteCreationEntityInput(CreationEntityInput):
    expected_revision: int


class ListArtifactVersionsInput(CreationArtifactInput):
    limit: int = 100


class ArtifactVersionDiffInput(CompatibleInput):
    version_id: str
    against_version_id: str = ""


class RestoreArtifactVersionInput(CompatibleInput):
    version_id: str
    expected_revision: int


class ConfirmCreationArtifactInput(CreationArtifactInput):
    expected_revision: int


class ModelBackedCreationArtifactInput(CreationArtifactInput):
    model: str = Field(default="", description=_CREATION_MODEL_DESCRIPTION)
    use_model: bool = True
    context_entity_ids: list[str] = Field(default_factory=list, max_length=24)
    context_artifacts: list[str] = Field(default_factory=list, max_length=6)


class GenerateCreationArtifactInput(ModelBackedCreationArtifactInput):
    expected_revision: int
    entity_type: str = ""
    instruction: str = ""


class RefineCreationArtifactInput(ModelBackedCreationArtifactInput):
    expected_revision: int
    instruction: str
    entity_id: str = ""


class RegenerateCreationArtifactInput(ModelBackedCreationArtifactInput):
    expected_revision: int
    instruction: str = ""
    entity_id: str = ""


class CreationOperationInput(CompatibleInput):
    operation_id: str


class ImportCreationMaterialInput(CompatibleInput):
    session_id: str
    file_path: str
    model: str = ""
    source_message_id: str = ""


class PreviewCreationImportInput(CompatibleInput):
    session_id: str
    import_id: str


class ApplyCreationImportInput(CompatibleInput):
    import_id: str
    selected_artifacts: list[
        Literal[
            "world_style",
            "characters",
            "locations",
            "macro_outline",
            "opening_outline",
        ]
    ]
    strategy: Literal["merge", "overwrite_unconfirmed", "skip_conflicts"] = "merge"
    expected_revision: int


class ListImportedFilesInput(CompatibleInput):
    cursor: int = Field(default=0, ge=0, description="Cursor returned by the previous page")
    limit: int = Field(default=3, ge=1, le=3, description="Files in this page (default/max 3)")


class ReadImportedFileInput(CompatibleInput):
    filename: str = Field(description="Name of the file to read (from list_imported_files)")
    max_size: int = Field(
        default=4_000,
        ge=1,
        le=4_000,
        description="Characters in this range (default/max 4000)",
    )
    offset_chars: int = Field(
        default=0,
        ge=0,
        description="Character offset for this range (default 0)",
    )


_INPUTS: dict[str, type[BaseModel]] = {
    "start_novel_creation_session": StartNovelCreationSessionInput,
    "get_creation_session": CreationSessionInput,
    "get_creation_snapshot": CreationSessionInput,
    "get_creation_operation": GetCreationOperationInput,
    "patch_creation_session": PatchCreationSessionInput,
    "get_creation_artifact": CreationArtifactInput,
    "list_creation_artifacts": ListCreationArtifactsInput,
    "get_creation_dependencies": CreationArtifactInput,
    "get_creation_dependency_graph": ListCreationArtifactsInput,
    "validate_creation_consistency": ListCreationArtifactsInput,
    "patch_creation_artifact": PatchCreationArtifactInput,
    "lock_creation_fields": CreationArtifactLockInput,
    "unlock_creation_fields": CreationArtifactLockInput,
    "undo_creation_artifact": UndoCreationArtifactInput,
    "list_creation_entities": ListCreationEntitiesInput,
    "get_creation_entity": CreationEntityInput,
    "patch_creation_entity": PatchCreationEntityInput,
    "delete_creation_entity": DeleteCreationEntityInput,
    "list_creation_artifact_versions": ListArtifactVersionsInput,
    "get_creation_artifact_diff": ArtifactVersionDiffInput,
    "restore_creation_artifact_version": RestoreArtifactVersionInput,
    "confirm_creation_artifact": ConfirmCreationArtifactInput,
    "generate_creation_artifact": GenerateCreationArtifactInput,
    "refine_creation_artifact": RefineCreationArtifactInput,
    "regenerate_creation_artifact": RegenerateCreationArtifactInput,
    "cancel_creation_operation": CreationOperationInput,
    "pause_creation_operation": CreationOperationInput,
    "resume_creation_operation": CreationOperationInput,
    "retry_creation_operation": CreationOperationInput,
    "validate_creation_session": CreationSessionInput,
    "finalize_creation_session": CreationSessionInput,
    "import_creation_material": ImportCreationMaterialInput,
    "preview_creation_import": PreviewCreationImportInput,
    "apply_creation_import": ApplyCreationImportInput,
    "list_imported_files": ListImportedFilesInput,
    "read_imported_file": ReadImportedFileInput,
}


def build_creation_tool_specs(definitions: Mapping[str, Any]) -> list[ToolSpec]:
    specs: list[ToolSpec] = []
    for name, input_model in _INPUTS.items():
        tool = definitions[name]
        specs.append(
            project_typed_tool_spec(
                tool,
                input_model=input_model,
                version="3.0.0",
            )
        )
    return specs


__all__ = ["build_creation_tool_specs"]
