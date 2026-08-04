"""Typed contracts for new-novel workspace tools."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ....architecture.tool_spec import ToolSpec, project_typed_tool_spec


class CompatibleInput(BaseModel):
    model_config = ConfigDict(extra="allow", protected_namespaces=())


class StartNovelCreationSessionInput(CompatibleInput):
    mode: Literal["internal_llm", "external_agent"] = "external_agent"
    user_brief: str = ""
    target_audience: str = ""
    genre: str = ""
    platform: str = ""


class DraftNovelBlueprintInput(CompatibleInput):
    session_id: str
    execution_mode: Literal["template", "hybrid", "internal_llm", "external_agent"] = "template"
    user_brief: str = ""
    feedback: str = ""
    revision_mode: Literal["initial", "refine", "regenerate"] = "initial"
    enhance_with_llm: bool = False
    skip_questions: bool = False
    depth: Literal["concept", "full"] = "full"


class ReviewNovelBlueprintInput(CompatibleInput):
    session_id: str
    execution_mode: Literal["hybrid", "internal_llm", "external_agent"] = "hybrid"
    blueprint: dict[str, Any] = Field(default_factory=dict)


class ApplyNovelBlueprintInput(CompatibleInput):
    session_id: str
    blueprint_index: int = 0
    mode: Literal["manual", "auto"] = "manual"
    blueprint: dict[str, Any] = Field(default_factory=dict)


class GetNovelCreationSessionInput(CompatibleInput):
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


class ListCreationArtifactsInput(CompatibleInput):
    session_id: str


class PatchCreationArtifactInput(CreationArtifactInput):
    expected_revision: int
    changes: list[CreationPatchOperation]
    source: str = "assistant"


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


class CreationEntityInput(CompatibleInput):
    entity_id: str


class PatchCreationEntityInput(CreationEntityInput):
    expected_revision: int
    changes: list[CreationPatchOperation]
    source: str = "assistant"


class DeleteCreationEntityInput(CreationEntityInput):
    expected_revision: int
    source: str = "assistant"


class ListArtifactVersionsInput(CreationArtifactInput):
    limit: int = 100


class ArtifactVersionDiffInput(CompatibleInput):
    version_id: str
    against_version_id: str = ""


class RestoreArtifactVersionInput(CompatibleInput):
    version_id: str
    expected_revision: int


class GenerateNovelCreationStageInput(CompatibleInput):
    session_id: str
    stage: str
    model: str = ""
    use_model: bool = True
    auto_confirm: bool = False
    session_patch: dict[str, Any] = Field(default_factory=dict)
    operation: str = "generate"
    instruction: str = ""
    expected_revision: int | None = None
    entity_id: str = ""
    entity_type: str = ""


class SubmitNovelCreationStageInput(CompatibleInput):
    session_id: str
    stage: str
    data: dict[str, Any]
    confirm: bool = False
    source: str = "external_agent"


class ConfirmCreationArtifactInput(CreationArtifactInput):
    expected_revision: int
    data: dict[str, Any] = Field(default_factory=dict)
    source: Literal["author", "assistant", "external_agent"] = "assistant"


class GenerateCreationArtifactInput(CreationArtifactInput):
    expected_revision: int
    model: str = ""
    entity_type: str = ""
    instruction: str = ""


class RefineCreationArtifactInput(CreationArtifactInput):
    expected_revision: int
    instruction: str
    model: str = ""
    entity_id: str = ""


class RegenerateCreationArtifactInput(CreationArtifactInput):
    expected_revision: int
    instruction: str = ""
    model: str = ""
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
    pass


class ReadImportedFileInput(CompatibleInput):
    filename: str
    max_size: int = 50_000


_INPUTS: dict[str, type[BaseModel]] = {
    "start_novel_creation_session": StartNovelCreationSessionInput,
    "draft_novel_blueprint": DraftNovelBlueprintInput,
    "review_novel_blueprint": ReviewNovelBlueprintInput,
    "apply_novel_blueprint": ApplyNovelBlueprintInput,
    "get_novel_creation_session": GetNovelCreationSessionInput,
    "get_creation_session": GetNovelCreationSessionInput,
    "get_creation_snapshot": GetNovelCreationSessionInput,
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
    "generate_novel_creation_stage": GenerateNovelCreationStageInput,
    "submit_novel_creation_stage": SubmitNovelCreationStageInput,
    "confirm_creation_artifact": ConfirmCreationArtifactInput,
    "generate_creation_artifact": GenerateCreationArtifactInput,
    "refine_creation_artifact": RefineCreationArtifactInput,
    "regenerate_creation_artifact": RegenerateCreationArtifactInput,
    "cancel_creation_operation": CreationOperationInput,
    "pause_creation_operation": CreationOperationInput,
    "resume_creation_operation": CreationOperationInput,
    "retry_creation_operation": CreationOperationInput,
    "validate_creation_session": GetNovelCreationSessionInput,
    "finalize_creation_session": GetNovelCreationSessionInput,
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
