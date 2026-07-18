"""SQLAlchemy adapter for workspace-assistant persistence."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy.orm import Session

from app.database.models import (
    AssistantConversation,
    AssistantMemory,
    AssistantMessage,
    AssistantRun,
    AssistantRunStep,
    Chapter,
    ChapterCharacter,
    ChapterSnapshot,
    ChapterSummary,
    Character,
    OutlineNode,
    OutlineNodeCharacter,
)


class SqlAlchemyAssistantWorkspace:
    def __init__(self, session: Session) -> None:
        self.db = session

    def resolve_characters(
        self,
        project_id: str,
        names: Sequence[str],
        outline_node_id: str | None,
        *,
        limit: int,
    ) -> list[Any]:
        resolved: list[Any] = []
        seen: set[str] = set()
        clean_names = {name.strip() for name in names if name.strip()}
        if clean_names:
            characters = self.db.query(Character).filter(
                Character.project_id == project_id,
                Character.name.in_(clean_names),
            ).all()
            for character in characters:
                resolved.append(character)
                seen.add(character.id)
        if outline_node_id and len(resolved) < limit:
            links = self.db.query(OutlineNodeCharacter).join(
                OutlineNode,
                OutlineNode.id == OutlineNodeCharacter.outline_node_id,
            ).filter(
                OutlineNode.project_id == project_id,
                OutlineNodeCharacter.outline_node_id == outline_node_id,
            ).all()
            for link in links:
                if link.character and link.character.id not in seen:
                    resolved.append(link.character)
                    seen.add(link.character.id)
                if len(resolved) >= limit:
                    break
        if len(resolved) < limit:
            extras = self.db.query(Character).filter(
                Character.project_id == project_id
            ).order_by(
                Character.role_type.asc(),
                Character.updated_at.desc(),
            ).limit(limit * 2).all()
            for character in extras:
                if character.id not in seen:
                    resolved.append(character)
                    seen.add(character.id)
                if len(resolved) >= limit:
                    break
        return resolved[:limit]

    def create_chapter(self, **values: Any):
        chapter = Chapter(**values)
        self.db.add(chapter)
        return chapter

    def create_summary(self, **values: Any):
        summary = ChapterSummary(**values)
        self.db.add(summary)
        return summary

    def create_snapshot(self, **values: Any):
        snapshot = ChapterSnapshot(**values)
        self.db.add(snapshot)
        return snapshot

    def characters_by_names(self, project_id: str, names: set[str]):
        if not names:
            return []
        return self.db.query(Character).filter(
            Character.project_id == project_id,
            Character.name.in_(names),
        ).all()

    def clear_chapter_characters(self, chapter_id: str) -> None:
        self.db.query(ChapterCharacter).filter(
            ChapterCharacter.chapter_id == chapter_id
        ).delete()

    def link_chapter_character(self, **values: Any):
        link = ChapterCharacter(**values)
        self.db.add(link)
        return link

    def conversation(self, project_id: str, conversation_id: str):
        return self.db.query(AssistantConversation).filter(
            AssistantConversation.id == conversation_id,
            AssistantConversation.project_id == project_id,
        ).first()

    def create_conversation(self, **values: Any):
        conversation = AssistantConversation(**values)
        self.db.add(conversation)
        return conversation

    def create_message(self, **values: Any):
        message = AssistantMessage(**values)
        self.db.add(message)
        return message

    def conversation_messages(self, conversation_id: str):
        return self.db.query(AssistantMessage).filter(
            AssistantMessage.conversation_id == conversation_id
        ).order_by(
            AssistantMessage.created_at.asc(),
            AssistantMessage.role.desc(),
            AssistantMessage.updated_at.asc(),
            AssistantMessage.id.asc(),
        ).all()

    def previous_assistant_messages(self, conversation_id: str):
        return self.db.query(AssistantMessage).filter(
            AssistantMessage.conversation_id == conversation_id,
            AssistantMessage.role == "assistant",
            AssistantMessage.status.in_({"completed", "running"}),
        ).order_by(AssistantMessage.created_at.desc()).all()

    def conversations_with_counts(self, project_id: str, scope: str):
        conversations = self.db.query(AssistantConversation).filter(
            AssistantConversation.project_id == project_id,
            AssistantConversation.scope == scope,
        ).order_by(
            AssistantConversation.updated_at.desc(),
            AssistantConversation.created_at.desc(),
        ).all()
        return [
            (
                conversation,
                self.db.query(AssistantMessage).filter(
                    AssistantMessage.conversation_id == conversation.id
                ).count(),
            )
            for conversation in conversations
        ]

    def delete(self, value: Any) -> None:
        self.db.delete(value)

    def runs(self, project_id: str, conversation_id: str | None, *, limit: int):
        query = self.db.query(AssistantRun).filter(AssistantRun.project_id == project_id)
        if conversation_id:
            query = query.filter(AssistantRun.conversation_id == conversation_id)
        return query.order_by(AssistantRun.created_at.desc()).limit(limit).all()

    def run(self, project_id: str, run_id: str):
        return self.db.query(AssistantRun).filter(
            AssistantRun.project_id == project_id,
            AssistantRun.id == run_id,
        ).first()

    def run_steps(self, run_id: str):
        return self.db.query(AssistantRunStep).filter(
            AssistantRunStep.run_id == run_id
        ).order_by(
            AssistantRunStep.created_at.asc(),
            AssistantRunStep.id.asc(),
        ).all()

    def chapter(self, project_id: str, chapter_id: str):
        return self.db.query(Chapter).filter(
            Chapter.id == chapter_id,
            Chapter.project_id == project_id,
        ).first()

    def memories(self, project_id: str, categories: Sequence[str], *, limit: int):
        return self.db.query(AssistantMemory).filter(
            AssistantMemory.project_id == project_id,
            AssistantMemory.category.in_(categories),
        ).order_by(
            AssistantMemory.importance.desc(),
            AssistantMemory.updated_at.desc(),
        ).limit(limit).all()

    def related_memories(
        self,
        project_id: str,
        categories: Sequence[str],
        terms: Sequence[str],
        *,
        limit: int,
    ):
        query = self.db.query(AssistantMemory).filter(
            AssistantMemory.project_id == project_id,
            AssistantMemory.category.in_(categories),
        )
        for term in terms:
            query = query.filter(
                AssistantMemory.key.ilike(f"%{term}%")
                | AssistantMemory.value.ilike(f"%{term}%")
            )
        return query.order_by(AssistantMemory.importance.desc()).limit(limit).all()


__all__ = ["SqlAlchemyAssistantWorkspace"]
