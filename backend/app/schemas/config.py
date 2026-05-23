"""Pydantic schemas for API config and global model settings."""
from typing import Optional
from pydantic import BaseModel, Field
from datetime import datetime


class APIConfigCreate(BaseModel):
    """Schema for creating/updating an API config."""
    provider: str = Field(..., min_length=1, max_length=50, description="提供商标识")
    api_key: str = Field(..., min_length=1, description="API Key（明文，后端加密存储）")
    default_model: str = Field(..., min_length=1, max_length=100, description="默认模型名")
    base_url_override: Optional[str] = Field(None, max_length=500, description="自定义API端点")
    max_output_tokens: Optional[int] = Field(None, ge=1, le=1000000, description="该模型最大输出tokens")
    deconstruct_input_char_limit: Optional[int] = Field(None, ge=1, le=1000000, description="拆书合并输入字符上限")
    deconstruct_item_char_limit: Optional[int] = Field(None, ge=1, le=1000000, description="拆书单条内容字符上限")


class GlobalModelSetting(BaseModel):
    """Schema for global default model setting."""
    provider: str = Field(..., description="全局默认提供商")
    model: str = Field(..., description="全局默认模型名")


class ModelListRequest(BaseModel):
    """Schema for requesting available models from a provider."""
    provider: str = Field(..., min_length=1, max_length=50, description="提供商标识")
    api_key: str = Field(..., min_length=1, description="API Key（明文）")
    base_url_override: Optional[str] = Field(None, max_length=500, description="自定义API端点")


class ConnectionTestRequest(BaseModel):
    """Schema for testing API connection."""
    provider: str = Field(..., min_length=1, max_length=50, description="提供商标识")
    api_key: str = Field(..., min_length=1, description="API Key（明文）")
    base_url_override: Optional[str] = Field(None, max_length=500, description="自定义API端点")
