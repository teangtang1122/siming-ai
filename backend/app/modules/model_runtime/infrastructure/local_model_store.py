"""SQLAlchemy persistence adapter for local model management."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy.orm import Session

from .legacy_models import (
    LocalModel,
    LocalRuntimeInstallation,
    ModelAdapter,
    ModelDownloadTask,
    TrainingDataset,
    TrainingJob,
)
from .models import LocalModelTaskSetting


class SqlAlchemyLocalModelStore:
    def __init__(self, session: Session) -> None:
        self.session = session

    def catalog_models(self):
        return self.session.query(LocalModel).order_by(LocalModel.recommended_vram_gb.asc()).all()

    def runtime_installation(self, runtime_key: str):
        return self.session.query(LocalRuntimeInstallation).filter(
            LocalRuntimeInstallation.runtime_key == runtime_key
        ).first()

    def task_settings(self):
        return self.session.query(LocalModelTaskSetting).all()

    def installed_models(self):
        return self.session.query(LocalModel).filter(LocalModel.status == "installed").all()

    def download_tasks(self, *, limit: int = 100):
        return self.session.query(ModelDownloadTask).order_by(
            ModelDownloadTask.created_at.desc()
        ).limit(limit).all()

    def download_task(self, task_id: str):
        return self.session.query(ModelDownloadTask).filter(ModelDownloadTask.id == task_id).first()

    def model(self, model_key: str):
        return self.session.query(LocalModel).filter(LocalModel.model_key == model_key).first()

    def task_setting(self, task_type: str):
        return self.session.query(LocalModelTaskSetting).filter(
            LocalModelTaskSetting.task_type == task_type
        ).first()

    def create_task_setting(self, task_type: str, model_key: str):
        row = LocalModelTaskSetting(task_type=task_type, model_key=model_key)
        self.session.add(row)
        return row

    def delete(self, value: Any) -> None:
        self.session.delete(value)

    def adapters(self, project_id: str | None = None):
        query = self.session.query(ModelAdapter)
        if project_id:
            query = query.filter(
                (ModelAdapter.project_id == project_id) | (ModelAdapter.project_id.is_(None))
            )
        return query.order_by(ModelAdapter.created_at.desc()).all()

    def adapter(self, adapter_id: str):
        return self.session.query(ModelAdapter).filter(ModelAdapter.id == adapter_id).first()

    def selected_adapters(self, adapter_ids: Sequence[str], model_key: str):
        if not adapter_ids:
            return []
        return self.session.query(ModelAdapter).filter(
            ModelAdapter.id.in_(adapter_ids),
            ModelAdapter.base_model_key == model_key,
        ).all()

    def datasets(self, project_id: str | None = None):
        query = self.session.query(TrainingDataset)
        if project_id:
            query = query.filter(TrainingDataset.project_id == project_id)
        return query.order_by(TrainingDataset.created_at.desc()).all()

    def training_jobs(self, project_id: str | None = None):
        query = self.session.query(TrainingJob)
        if project_id:
            query = query.filter(TrainingJob.project_id == project_id)
        return query.order_by(TrainingJob.created_at.desc()).all()

    def training_job(self, job_id: str):
        return self.session.query(TrainingJob).filter(TrainingJob.id == job_id).first()


__all__ = ["SqlAlchemyLocalModelStore"]
