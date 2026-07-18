"""SQLAlchemy persistence models owned by the integrations module."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from app.database.models_support import generate_uuid
from app.database.session import Base


def default_external_agent_enabled_packs() -> list[str]:
    return [
        "readonly_collaboration",
        "project_writing",
        "project_management",
        "trusted_local_maintenance",
    ]


def default_trusted_local_clients() -> list[str]:
    return ["claude-code", "codex", "opencode", "mimocode", "cursor", "trae"]


class Skill(Base):
    __tablename__ = "skills"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    builtin_key = Column(String(50), nullable=True)  # e.g. "continue_writing"
    name = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)
    trigger_examples = Column(Text, nullable=True)  # JSON array of keyword strings
    system_prompt = Column(Text, nullable=False)
    recommended_tools = Column(Text, nullable=True)  # JSON array of tool names
    forbidden_tools = Column(Text, nullable=True)  # JSON array of tool names
    scope = Column(
        String(30), default="global"
    )  # global|project|writing|outline|characters|worldbuilding|cataloging|research
    priority = Column(Integer, default=0)
    enabled = Column(Boolean, default=True)
    is_builtin = Column(Boolean, default=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    project = relationship("Project", back_populates="skills")

    __table_args__ = (Index("ix_skills_project_name", "project_id", "name", unique=True),)


class SkillVersion(Base):
    __tablename__ = "skill_versions"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    skill_id = Column(String(36), ForeignKey("skills.id", ondelete="CASCADE"), nullable=False)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    title = Column(String(200), nullable=False)
    change_summary = Column(Text, nullable=True)
    snapshot_json = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    skill = relationship("Skill")

    __table_args__ = (Index("ix_skill_versions_skill_created", "skill_id", "created_at"),)


class McpServerConfig(Base):
    __tablename__ = "mcp_server_configs"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(100), nullable=False)
    transport = Column(String(20), nullable=False, default="stdio")  # stdio | http
    command = Column(Text, nullable=True)  # for stdio: command to run
    url = Column(String(500), nullable=True)  # for http: server URL
    enabled = Column(Boolean, default=True)
    status = Column(
        String(20), default="disconnected"
    )  # disconnected | connecting | connected | error
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=True, onupdate=datetime.utcnow)

    project = relationship("Project", back_populates="mcp_server_configs")

    __table_args__ = (Index("ix_mcp_server_configs_project", "project_id"),)


class ExternalAgentSettings(Base):
    __tablename__ = "external_agent_settings"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    project_id = Column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    enabled_packs = Column(JSON, nullable=False, default=default_external_agent_enabled_packs)
    trusted_local_enabled = Column(Boolean, default=True)
    trusted_local_clients = Column(JSON, nullable=False, default=default_trusted_local_clients)
    require_confirmation_for_writes = Column(Boolean, default=False)
    require_confirmation_for_destructive = Column(Boolean, default=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=True, onupdate=datetime.utcnow)

    project = relationship("Project", back_populates="external_agent_settings")

    __table_args__ = (Index("ix_external_agent_settings_project", "project_id", unique=True),)


class PublicPromptPack(Base):
    __tablename__ = "public_prompt_packs"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    project_id = Column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=True
    )  # null = global builtin
    pack_id = Column(String(100), nullable=False)  # e.g. "chapter_writing_quality"
    version = Column(String(20), nullable=False, default="1.0.0")
    scope = Column(String(50), nullable=False)  # new_project|chapter_writing|chapter_review|...
    title = Column(String(200), nullable=False)
    summary = Column(Text, nullable=True)
    system_prompt = Column(Text, nullable=False)
    workflow_json = Column(JSON, nullable=True)  # list of workflow steps
    quality_rubric_json = Column(JSON, nullable=True)
    tool_playbook_json = Column(JSON, nullable=True)
    forbidden_patterns_json = Column(JSON, nullable=True)  # list of strings
    context_policy_json = Column(JSON, nullable=True)
    output_contract_json = Column(JSON, nullable=True)
    enabled = Column(Boolean, default=True)
    is_builtin = Column(Boolean, default=False)
    tags_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=True, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("ix_public_prompt_packs_project_pack", "project_id", "pack_id"),
        Index("ix_public_prompt_packs_scope", "scope"),
    )


class MethodCard(Base):
    __tablename__ = "method_cards"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    project_id = Column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=True
    )  # null = global builtin
    card_id = Column(String(100), nullable=False)  # e.g. "chapter_writing_workflow"
    version = Column(String(20), nullable=False, default="1.0.0")
    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    content_json = Column(JSON, nullable=False)  # structured method content
    card_type = Column(String(50), nullable=False)  # workflow|rubric|playbook|pattern
    enabled = Column(Boolean, default=True)
    is_builtin = Column(Boolean, default=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=True, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("ix_method_cards_project_card", "project_id", "card_id"),
        Index("ix_method_cards_type", "card_type"),
    )


class ExternalAgentGlobalSettings(Base):
    __tablename__ = "external_agent_global_settings"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    enabled_packs = Column(JSON, nullable=False, default=default_external_agent_enabled_packs)
    trusted_local_enabled = Column(Boolean, default=True)
    trusted_local_clients = Column(JSON, nullable=False, default=default_trusted_local_clients)
    require_confirmation_for_writes = Column(Boolean, default=False)
    require_confirmation_for_destructive = Column(Boolean, default=False)
    mcp_permission_source = Column(
        String(30), default="global_settings"
    )  # global_settings | cli_override
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=True, onupdate=datetime.utcnow)


__all__ = [
    "default_external_agent_enabled_packs",
    "default_trusted_local_clients",
    "Skill",
    "SkillVersion",
    "McpServerConfig",
    "ExternalAgentSettings",
    "PublicPromptPack",
    "MethodCard",
    "ExternalAgentGlobalSettings",
]
