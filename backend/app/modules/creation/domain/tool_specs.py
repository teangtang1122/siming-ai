"""Typed contracts for new-novel workspace tools."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ....architecture.tool_spec import ToolSpec, project_typed_tool_spec
from ....services.novel_creation_contract import STAGE_ORDER
from .entity_contract import ENTITY_TYPES_BY_ARTIFACT


class CompatibleInput(BaseModel):
    model_config = ConfigDict(extra="allow", protected_namespaces=())


_CREATION_MODEL_DESCRIPTION = (
    "Optional model identity. When omitted, creation uses the active default model."
)

_PATCH_CHANGES_DESCRIPTION = (
    "原生 JSON 操作数组，不是 JSON 编码字符串。每项必须有 path，"
    "action 与标准 JSON Patch op 二选一；"
    "完整阶段使用 [{\"path\":\"/\",\"action\":\"set\",\"value\":{...}}]，"
    "写入动作须传原生 JSON value；remove 不需要 value，resize 须传 target_count。"
)


def _creation_patch_json_schema(schema: dict[str, Any]) -> None:
    """Publish one compact operation schema to every transport and model."""
    properties = schema["properties"]
    for field_name in ("action", "op", "target_count"):
        property_schema = properties[field_name]
        non_null = next(
            branch for branch in property_schema.pop("anyOf")
            if branch.get("type") != "null"
        )
        property_schema.pop("default", None)
        property_schema.update(non_null)
    schema["oneOf"] = [{"required": ["action"]}, {"required": ["op"]}]
    schema["allOf"] = [
        {
            "if": {
                "required": ["action"],
                "properties": {"action": {"enum": ["set", "replace", "append"]}},
            },
            "then": {"required": ["value"]},
        },
        {
            "if": {
                "required": ["op"],
                "properties": {"op": {"enum": ["add", "replace"]}},
            },
            "then": {"required": ["value"]},
        },
        {
            "if": {"required": ["action"], "properties": {"action": {"const": "resize"}}},
            "then": {"required": ["target_count"]},
        },
    ]


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


class CreationSessionFormPatch(CompatibleInput):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"minProperties": 1})

    brief: str = Field(
        default="", description="仅在作者明确给出或修改整书意向时填写；不转存设定细节",
    )
    preset_id: str = ""
    theme_id: str = ""
    genre: str = Field(default="", description="作品题材，例如玄幻")
    target_audience: str = ""
    platform: str = ""
    target_words: int = Field(default=600000, ge=10000, le=10000000)
    target_chapters: int = Field(default=240, ge=1, le=5000)
    world_tone: str = ""
    story_structure: str = ""
    pacing: str = ""
    writing_style: str = ""
    special_requirements: list[str] = Field(
        default_factory=list,
        description=(
            "仅作者额外指定的全书硬约束；不复述 brief 中的书名、角色、世界或剧情，没有则填空数组"
        ),
    )
    avoid: list[str] = Field(
        default_factory=list, description="作者明确禁止的全书内容或写法；不是设定暂存区",
    )
    author_overrides: dict[str, Any] = Field(default_factory=dict)


class CreationSessionChanges(CompatibleInput):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"minProperties": 1})

    form: CreationSessionFormPatch = Field(
        default_factory=CreationSessionFormPatch,
        description="仅存作者明确的全书创作要求；剧情、角色和世界事实写对应阶段资料",
    )
    selected_concept_id: str = ""
    quick_mode: bool = False
    creation_mode: Literal["author_led", "explore"] = "explore"
    author_brief: str = ""
    author_outline: str = ""
    locked_requirements: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_effective_fields(self) -> CreationSessionChanges:
        if not self.model_fields_set:
            raise ValueError("changes 必须包含要修改的字段")
        if "form" in self.model_fields_set and not self.form.model_fields_set:
            raise ValueError("form 必须包含要修改的创作约束字段")
        return self


class PatchCreationSessionInput(CompatibleInput):
    session_id: str
    expected_revision: int
    changes: CreationSessionChanges = Field(
        description="会话级修改；例如 {\"form\":{\"genre\":\"玄幻\",\"target_words\":600000}}",
        examples=[{"form": {"genre": "玄幻", "target_words": 600000}}],
    )


class CreationArtifactInput(CompatibleInput):
    session_id: str
    artifact: str


class GetCreationArtifactInput(CreationArtifactInput):
    artifact: str = Field(
        description=(
            "必须使用立项快照 artifacts 中的阶段 ID；全书卷纲为 macro_outline，"
            "前三章细纲为 opening_outline。"
        ),
        json_schema_extra={"enum": list(STAGE_ORDER)},
    )


class CreationPatchOperation(CompatibleInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra=_creation_patch_json_schema,
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
        min_length=1,
        pattern=r"^/",
        description=(
            "必填 JSON Pointer；完整阶段用 /，"
            "局部字段如 /special_requirements 或 /volumes/0/title"
        ),
    )
    value: Any = Field(default=None, description="set、replace、append 或 add 写入的值")
    target_count: int | None = Field(default=None, ge=0, description="resize 的目标数组长度")
    fill_value: Any = Field(default=None, description="resize 扩展数组时使用的填充值")

    @model_validator(mode="after")
    def require_one_operation_form(self) -> CreationPatchOperation:
        if any(
            field_name in self.model_fields_set and getattr(self, field_name) is None
            for field_name in ("action", "op", "target_count")
        ):
            raise ValueError("action、op 或 target_count 不能显式传 null")
        if (self.action is None) == (self.op is None):
            raise ValueError("action 与 op 必须且只能提供一个")
        writes_value = self.action in {"set", "replace", "append"} or self.op in {
            "add", "replace",
        }
        if writes_value and "value" not in self.model_fields_set:
            raise ValueError("写入操作必须提供 value")
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
        examples=[[{"path": "/brief", "action": "set", "value": "作者确认的创作要求"}]],
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
        examples=[[{"path": "/name", "action": "set", "value": "作者确认的名称"}]],
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
    entity_type: str = Field(default="", json_schema_extra={
        "enum": ["", *sorted(set().union(*ENTITY_TYPES_BY_ARTIFACT.values()))],
    }, description="Optional isolated entity type. " + "; ".join(
        f"{artifact}: {', '.join(sorted(types))}"
        for artifact, types in ENTITY_TYPES_BY_ARTIFACT.items()
    ))
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
    "get_creation_artifact": GetCreationArtifactInput,
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
        spec = project_typed_tool_spec(
            tool,
            input_model=input_model,
            version="3.0.0",
        )
        if name in {"patch_creation_artifact", "patch_creation_entity"}:
            # Inline the same operation schema used by REST request bodies so
            # native tool parsers can see the required path inside array items.
            schema = input_model.model_json_schema()
            operation = schema.pop("$defs")["CreationPatchOperation"]
            schema["properties"]["changes"]["items"] = operation
            spec = replace(spec, input_schema_override=schema)
        elif name == "patch_creation_session":
            # Inline both levels for native model parsers. REST, MCP and the
            # runtime still derive their fields from these same typed models.
            schema = input_model.model_json_schema()
            schema_defs = schema.pop("$defs")
            changes_schema = schema_defs["CreationSessionChanges"]
            changes_schema["properties"]["form"] = schema_defs["CreationSessionFormPatch"]
            schema["properties"]["changes"] = changes_schema
            spec = replace(spec, input_schema_override=schema)
        specs.append(spec)
    return specs


__all__ = [
    "build_creation_tool_specs", "CreationPatchOperation", "CreationSessionChanges",
    "CreationSessionFormPatch",
]
