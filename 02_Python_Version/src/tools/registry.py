"""Tool Schema、注册、校验与统一执行入口。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .handoff import HANDOFF_SCHEMA, handoff_to_human, initialize_handoff_database
from .status import STATUS_SCHEMA, initialize_status_database, query_application_status


@dataclass(frozen=True)
class RegisteredTool:
    schema: dict[str, object]
    handler: Callable[..., dict[str, object]]


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, RegisteredTool] = {}
        self.logger = logging.getLogger("campus_agent.tools")

    def register(self, schema: dict[str, object], handler: Callable[..., dict[str, object]]) -> None:
        name = str(schema.get("name", ""))
        if not name or name in self._tools:
            raise ValueError(f"Tool 名称为空或重复: {name}")
        self._tools[name] = RegisteredTool(schema=schema, handler=handler)

    @property
    def schemas(self) -> list[dict[str, object]]:
        return [tool.schema for tool in self._tools.values()]

    def execute(self, name: str, arguments: dict[str, object]) -> dict[str, object]:
        registered = self._tools.get(name)
        if not registered:
            return {"ok": False, "error": "unknown_tool", "message": f"未注册 Tool: {name}"}
        parameters = registered.schema.get("parameters", {})
        required = parameters.get("required", []) if isinstance(parameters, dict) else []
        missing = [field for field in required if field not in arguments]
        if missing:
            return {"ok": False, "error": "missing_arguments", "message": f"缺少参数: {', '.join(missing)}"}
        allowed = set(parameters.get("properties", {})) if isinstance(parameters, dict) else set()
        unexpected = set(arguments) - allowed
        if unexpected:
            return {"ok": False, "error": "unexpected_arguments", "message": f"多余参数: {', '.join(sorted(unexpected))}"}
        try:
            result = registered.handler(**arguments)
        except Exception as exc:
            self.logger.exception("tool=%s failed", name)
            return {"ok": False, "error": "tool_exception", "message": f"Tool 执行失败: {type(exc).__name__}"}
        self.logger.info("tool=%s arguments=%s ok=%s", name, arguments, result.get("ok"))
        return result


def build_default_registry(database_path: Path) -> ToolRegistry:
    initialize_status_database(database_path)
    initialize_handoff_database(database_path)
    registry = ToolRegistry()
    registry.register(
        STATUS_SCHEMA,
        lambda student_id, application_id: query_application_status(
            database_path, student_id, application_id
        ),
    )
    registry.register(HANDOFF_SCHEMA, lambda reason: handoff_to_human(database_path, reason))
    return registry
