"""Shared database query helpers used by routers."""
from sqlalchemy.orm import Session

from .exceptions import NotFoundError
from ..database.models import Character, Project


def get_project_or_404(db: Session, project_id: str) -> Project:
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise NotFoundError("作品不存在")
    return project


def get_character_or_404(db: Session, project_id: str, character_id: str) -> Character:
    character = (
        db.query(Character)
        .filter(Character.id == character_id, Character.project_id == project_id)
        .first()
    )
    if not character:
        raise NotFoundError("角色不存在")
    return character
