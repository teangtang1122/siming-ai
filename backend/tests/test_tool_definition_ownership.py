"""Architecture regressions for domain-owned workspace tool declarations."""

from app.modules.assistant.interfaces.tool_definitions import (
    TOOL_DEFINITIONS as ASSISTANT_TOOLS,
)
from app.modules.context.interfaces.tool_definitions import TOOL_DEFINITIONS as CONTEXT_TOOLS
from app.modules.continuity.interfaces.tool_definitions import (
    TOOL_DEFINITIONS as CONTINUITY_TOOLS,
)
from app.modules.creation.interfaces.tool_definitions import TOOL_DEFINITIONS as CREATION_TOOLS
from app.modules.integrations.interfaces.tool_definitions import (
    TOOL_DEFINITIONS as INTEGRATION_TOOLS,
)
from app.modules.model_runtime.interfaces.tool_definitions import (
    TOOL_DEFINITIONS as MODEL_RUNTIME_TOOLS,
)
from app.modules.operations.interfaces.tool_definitions import (
    TOOL_DEFINITIONS as OPERATION_TOOLS,
)
from app.modules.story.interfaces.tool_definitions import TOOL_DEFINITIONS as STORY_TOOLS
from app.services.workspace.registry import _TOOL_REGISTRATION_ORDER, registry

DOMAIN_TOOL_GROUPS = (
    ASSISTANT_TOOLS,
    CONTEXT_TOOLS,
    CONTINUITY_TOOLS,
    CREATION_TOOLS,
    INTEGRATION_TOOLS,
    MODEL_RUNTIME_TOOLS,
    OPERATION_TOOLS,
    STORY_TOOLS,
)


def test_domain_tool_declarations_form_the_complete_registry() -> None:
    declarations = [tool for group in DOMAIN_TOOL_GROUPS for tool in group]
    names = [tool.name for tool in declarations]

    assert len(names) == 160
    assert len(set(names)) == len(names)
    assert set(names) == set(_TOOL_REGISTRATION_ORDER)
    assert registry.all_names() == list(_TOOL_REGISTRATION_ORDER)


def test_every_domain_tool_binds_its_declared_handler() -> None:
    for group in DOMAIN_TOOL_GROUPS:
        for declaration in group:
            registered = registry.get(declaration.name)
            assert registered is not None
            assert registered.handler_name == declaration.handler_name
            assert registered.handler is not None
            assert registered.handler.__name__ == registered.handler_name
