"""Writing statistics HTTP interface."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from ..core.response import ApiResponse
from ..modules.story.application.commands import StoryCommandContext
from ..modules.story.application.statistics import StoryStatistics
from ..modules.story.interfaces.dependencies import get_story_command
from ..modules.story.interfaces.statistics_dependencies import get_story_statistics
from ..schemas.stats import GoalUpdate

router = APIRouter(tags=["stats"])


@router.get("/projects/{project_id}/stats/today")
def get_today_stats(
    project_id: str,
    statistics: Annotated[StoryStatistics, Depends(get_story_statistics)],
):
    """Get today's writing statistics."""
    return ApiResponse.success(data=statistics.today(project_id))


@router.get("/projects/{project_id}/stats/history")
def get_stats_history(
    project_id: str,
    statistics: Annotated[StoryStatistics, Depends(get_story_statistics)],
    days: int = Query(7, ge=1, le=365, description="Number of days to look back"),
):
    """Get historical daily writing statistics."""
    return ApiResponse.success(data=statistics.history(project_id, days))


@router.put("/projects/{project_id}/stats/goal")
def set_daily_goal(
    project_id: str,
    payload: GoalUpdate,
    statistics: Annotated[StoryStatistics, Depends(get_story_statistics)],
    command: Annotated[StoryCommandContext, Depends(get_story_command)],
):
    """Set the daily word count goal in the request transaction."""
    data = statistics.set_daily_goal(project_id, payload.daily_word_goal)
    command.finish()
    return ApiResponse.success(
        data=data,
        message=f"每日目标已更新为 {data['daily_word_goal']} 字",
    )
