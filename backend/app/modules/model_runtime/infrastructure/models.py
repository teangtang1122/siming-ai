"""Persistence models owned by model runtime."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, Column, DateTime, Integer, String, Text

from ....database.models_support import generate_uuid
from ....database.session import Base


class APIConfig(Base):
    __tablename__ = "api_configs"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    provider = Column(String(50), nullable=False, unique=True)
    api_key_encrypted = Column(Text, nullable=False)
    default_model = Column(String(512), nullable=False)
    is_global_default = Column(Boolean, default=False)
    base_url_override = Column(String(500), nullable=True)
    api_protocol = Column(String(30), nullable=False, default="auto")
    provider_type = Column(String(20), nullable=False, default="api")
    cli_command = Column(String(500), nullable=True)
    cli_args = Column(Text, nullable=True)
    readiness_status = Column(String(30), nullable=False, default="unverified")
    readiness_json = Column(Text, nullable=True)
    last_tested_at = Column(DateTime, nullable=True)
    max_output_tokens = Column(Integer, nullable=True)
    deconstruct_input_char_limit = Column(Integer, nullable=True)
    deconstruct_item_char_limit = Column(Integer, nullable=True)
    available_models_json = Column(JSON, nullable=True)
    model_request_parameters = Column(JSON, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class ModelTaskSetting(Base):
    __tablename__ = "model_task_settings"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    task_type = Column(String(30), nullable=False, unique=True)
    provider = Column(String(50), nullable=False)
    model_name = Column(String(512), nullable=False)
    adapter_ids = Column(JSON, nullable=True)
    context_length = Column(Integer, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
