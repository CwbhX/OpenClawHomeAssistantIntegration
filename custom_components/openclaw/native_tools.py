"""Native Home Assistant tool routing and capability helpers for OpenClaw."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
from pathlib import Path
from time import perf_counter
from typing import Any, Callable

import yaml

CONF_ENABLE_NATIVE_HA_TOOLS = "enable_native_ha_tools"

DEFAULT_CAPABILITY_SUMMARY_LIMIT = 8

EXECUTE_SERVICE_TOOL_NAMES = {"execute_service", "execute_services"}
NATIVE_TOOL_NAMES = {
    "ha_inventory_query",
    "ha_automation_manage",
    "ha_scene_manage",
    "ha_script_manage",
    "ha_blueprint_manage",
}
SUPPORTED_TOOL_NAMES = EXECUTE_SERVICE_TOOL_NAMES | NATIVE_TOOL_NAMES

RESOURCE_AUTOMATION = "automation"
RESOURCE_SCENE = "scene"
RESOURCE_SCRIPT = "script"
RESOURCE_BLUEPRINT = "blueprint"

EDITABLE_RESOURCE_FILES: dict[str, str] = {
    RESOURCE_AUTOMATION: "automations.yaml",
    RESOURCE_SCENE: "scenes.yaml",
    RESOURCE_SCRIPT: "scripts.yaml",
}

RESOURCE_LIST_ACTIONS = {
    RESOURCE_AUTOMATION: "list_automations",
    RESOURCE_SCENE: "list_scenes",
    RESOURCE_SCRIPT: "list_scripts",
    RESOURCE_BLUEPRINT: "list_blueprints",
}
RESOURCE_GET_ACTIONS = {
    RESOURCE_AUTOMATION: "get_automation",
    RESOURCE_SCENE: "get_scene",
    RESOURCE_SCRIPT: "get_script",
    RESOURCE_BLUEPRINT: "get_blueprint",
}


class NativeToolError(Exception):
    """Raised when a native tool request cannot be satisfied."""


@dataclass(slots=True)
class ToolExecutionResult:
    """Normalized result for either gateway-style or native HA tool execution."""

    tool_name: str
    action: str | None
    ok: bool
    result: Any = None
    error: str | None = None
    duration_ms: int = 0
    resource_type: str | None = None
    target_id: str | None = None

    def result_preview(self, max_len: int = 400) -> str | None:
        """Return a compact preview string for the result payload."""
        value = self.result
        if value is None:
            return None
        if isinstance(value, str):
            text = value.strip()
        else:
            try:
                text = json.dumps(value, ensure_ascii=False, sort_keys=True)
            except (TypeError, ValueError):
                text = str(value)
        if not text:
            return None
        if len(text) > max_len:
            return f"{text[:max_len]}..."
        return text

    def to_follow_up_summary(self) -> dict[str, Any]:
        """Return a compact serializable summary for the second model round-trip."""
        summary: dict[str, Any] = {
            "tool": self.tool_name,
            "ok": self.ok,
            "duration_ms": self.duration_ms,
        }
        if self.action:
            summary["action"] = self.action
        if self.resource_type:
            summary["resource_type"] = self.resource_type
        if self.target_id:
            summary["target_id"] = self.target_id
        if self.error:
            summary["error"] = self.error
        preview = self.result_preview()
        if preview:
            summary["result_preview"] = preview
        return summary


def extract_tool_calls(response: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract OpenAI-compatible tool calls from a response payload."""
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        return []

    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    if not isinstance(message, dict):
        return []

    tool_calls = message.get("tool_calls")
    if not isinstance(tool_calls, list):
        return []

    return [call for call in tool_calls if isinstance(call, dict)]


def build_capabilities_payload(
    hass: Any,
    *,
    enabled: bool,
    entity_limit: int = 50,
    summary_limit: int | None = None,
) -> dict[str, Any]:
    """Return the native HA capability manifest."""
    limit = summary_limit if summary_limit is not None else entity_limit
    inventories = _build_inventories(hass, entity_limit=entity_limit)
    payload: dict[str, Any] = {
        "native_tools_enabled": enabled,
        "tool_families": sorted(NATIVE_TOOL_NAMES) if enabled else [],
        "manageable_resources": {
            RESOURCE_AUTOMATION: enabled,
            RESOURCE_SCENE: enabled,
            RESOURCE_SCRIPT: enabled,
            RESOURCE_BLUEPRINT: enabled,
        },
        "supported_actions": {
            "ha_inventory_query": [
                "list_capabilities",
                "list_entities",
                "list_automations",
                "list_scenes",
                "list_scripts",
                "list_blueprints",
                "get_automation",
                "get_scene",
                "get_script",
                "get_blueprint",
            ],
            "ha_automation_manage": [
                "list",
                "get",
                "create",
                "update",
                "replace",
                "delete",
                "enable",
                "disable",
            ],
            "ha_scene_manage": ["list", "get", "create", "update", "replace", "delete"],
            "ha_script_manage": ["list", "get", "create", "update", "replace", "delete"],
            "ha_blueprint_manage": ["list", "get", "create", "update", "replace", "delete"],
        },
        "inventory": {
            "entities": _trim_items(inventories["entities"], limit),
            RESOURCE_AUTOMATION: _trim_items(inventories[RESOURCE_AUTOMATION], limit),
            RESOURCE_SCENE: _trim_items(inventories[RESOURCE_SCENE], limit),
            RESOURCE_SCRIPT: _trim_items(inventories[RESOURCE_SCRIPT], limit),
            RESOURCE_BLUEPRINT: _trim_items(inventories[RESOURCE_BLUEPRINT], limit),
        },
        "counts": {
            "entities": len(inventories["entities"]),
            RESOURCE_AUTOMATION: len(inventories[RESOURCE_AUTOMATION]),
            RESOURCE_SCENE: len(inventories[RESOURCE_SCENE]),
            RESOURCE_SCRIPT: len(inventories[RESOURCE_SCRIPT]),
            RESOURCE_BLUEPRINT: len(inventories[RESOURCE_BLUEPRINT]),
        },
    }
    return payload


def build_capabilities_prompt(
    hass: Any,
    *,
    enabled: bool,
    entity_limit: int = DEFAULT_CAPABILITY_SUMMARY_LIMIT,
) -> str | None:
    """Build a compact prompt block advertising native HA capabilities."""
    if not enabled:
        return None

    payload = build_capabilities_payload(
        hass,
        enabled=enabled,
        entity_limit=entity_limit,
        summary_limit=entity_limit,
    )

    lines = [
        "Home Assistant management capabilities exposed by this integration:",
        f"- native_tools_enabled: {payload['native_tools_enabled']}",
        f"- tool_families: {', '.join(payload['tool_families'])}",
        "- manageable_resources:",
    ]
    for resource_type, is_enabled in payload["manageable_resources"].items():
        lines.append(f"  - {resource_type}: {is_enabled}")

    lines.append("- resource_counts:")
    for key, count in payload["counts"].items():
        lines.append(f"  - {key}: {count}")

    inventory = payload["inventory"]
    for resource_type in (RESOURCE_AUTOMATION, RESOURCE_SCENE, RESOURCE_SCRIPT, RESOURCE_BLUEPRINT):
        items = inventory.get(resource_type, [])
        if not items:
            continue
        lines.append(f"- {resource_type}_summary:")
        for item in items:
            name = item.get("name") or item.get("id")
            enabled_text = item.get("enabled")
            lines.append(
                "  - "
                f"id: {item.get('id')}; "
                f"name: {name}; "
                f"editable: {item.get('editable')}; "
                f"source: {item.get('source')}; "
                f"enabled: {enabled_text}"
            )

    lines.append(
        "Use these native HA tools for inventory and authoring before falling back to generic REST-style operations."
    )
    return "\n".join(lines)


async def async_execute_tool_calls(
    hass: Any,
    response: dict[str, Any],
    *,
    service_tools_enabled: bool,
    native_tools_enabled: bool,
    record_execution: Callable[[ToolExecutionResult], None] | None = None,
) -> list[ToolExecutionResult]:
    """Execute supported tool calls embedded in a model response."""
    results: list[ToolExecutionResult] = []
    for call in extract_tool_calls(response):
        function_data = call.get("function")
        if not isinstance(function_data, dict):
            continue

        tool_name = function_data.get("name")
        arguments = function_data.get("arguments")

        if tool_name not in SUPPORTED_TOOL_NAMES:
            results.append(
                _finalize_result(
                    ToolExecutionResult(
                        tool_name=str(tool_name),
                        action=None,
                        ok=False,
                        error=f"Unsupported tool '{tool_name}'",
                    ),
                    record_execution=record_execution,
                )
            )
            continue

        if tool_name in EXECUTE_SERVICE_TOOL_NAMES and not service_tools_enabled:
            results.append(
                _finalize_result(
                    ToolExecutionResult(
                        tool_name=tool_name,
                        action=None,
                        ok=False,
                        error="Service tool calls are disabled in the integration options",
                    ),
                    record_execution=record_execution,
                )
            )
            continue

        if tool_name in NATIVE_TOOL_NAMES and not native_tools_enabled:
            results.append(
                _finalize_result(
                    ToolExecutionResult(
                        tool_name=tool_name,
                        action=None,
                        ok=False,
                        error="Native Home Assistant management tools are disabled in the integration options",
                    ),
                    record_execution=record_execution,
                )
            )
            continue

        if not isinstance(arguments, str):
            results.append(
                _finalize_result(
                    ToolExecutionResult(
                        tool_name=str(tool_name),
                        action=None,
                        ok=False,
                        error="Tool arguments must be a JSON string",
                    ),
                    record_execution=record_execution,
                )
            )
            continue

        try:
            parsed_arguments = json.loads(arguments)
        except json.JSONDecodeError as err:
            results.append(
                _finalize_result(
                    ToolExecutionResult(
                        tool_name=str(tool_name),
                        action=None,
                        ok=False,
                        error=f"Invalid JSON tool arguments: {err}",
                    ),
                    record_execution=record_execution,
                )
            )
            continue

        started = perf_counter()
        try:
            if tool_name in EXECUTE_SERVICE_TOOL_NAMES:
                result_payload = await _async_execute_service_tool(
                    hass,
                    tool_name=tool_name,
                    payload=parsed_arguments,
                )
            else:
                result_payload = await _async_execute_native_tool(
                    hass,
                    tool_name=tool_name,
                    payload=parsed_arguments,
                )
            result = ToolExecutionResult(
                tool_name=tool_name,
                action=result_payload.get("action"),
                ok=True,
                result=result_payload.get("result"),
                resource_type=result_payload.get("resource_type"),
                target_id=result_payload.get("target_id"),
                duration_ms=int((perf_counter() - started) * 1000),
            )
        except NativeToolError as err:
            result = ToolExecutionResult(
                tool_name=tool_name,
                action=str(parsed_arguments.get("action")) if isinstance(parsed_arguments, dict) else None,
                ok=False,
                error=str(err),
                duration_ms=int((perf_counter() - started) * 1000),
                resource_type=_resource_type_for_tool(tool_name),
                target_id=_target_id_from_payload(tool_name, parsed_arguments),
            )

        results.append(_finalize_result(result, record_execution=record_execution))

    return results


def _finalize_result(
    result: ToolExecutionResult,
    *,
    record_execution: Callable[[ToolExecutionResult], None] | None = None,
) -> ToolExecutionResult:
    """Emit telemetry for a tool execution result and return it."""
    if record_execution:
        record_execution(result)
    return result


async def _async_execute_service_tool(hass: Any, *, tool_name: str, payload: Any) -> dict[str, Any]:
    """Execute the legacy service-call tool contract."""
    if not isinstance(payload, dict):
        raise NativeToolError("Service tool payload must be an object")

    services_list = payload.get("list")
    if not isinstance(services_list, list):
        raise NativeToolError("Service tool payload must contain a 'list' array")

    outcomes: list[dict[str, Any]] = []
    for item in services_list:
        if not isinstance(item, dict):
            continue
        domain = item.get("domain")
        service = item.get("service")
        service_data = item.get("service_data", {})
        if not isinstance(domain, str) or not isinstance(service, str):
            outcomes.append({"ok": False, "error": "Missing domain/service"})
            continue
        if not isinstance(service_data, dict):
            service_data = {}

        try:
            await hass.services.async_call(domain, service, service_data, blocking=True)
            outcomes.append(
                {
                    "ok": True,
                    "domain": domain,
                    "service": service,
                    "service_data": service_data,
                }
            )
        except Exception as err:  # noqa: BLE001
            outcomes.append(
                {
                    "ok": False,
                    "domain": domain,
                    "service": service,
                    "error": str(err),
                }
            )

    return {
        "action": "call_services",
        "resource_type": "service",
        "target_id": None,
        "result": {"calls": outcomes},
    }


async def _async_execute_native_tool(hass: Any, *, tool_name: str, payload: Any) -> dict[str, Any]:
    """Dispatch a native HA inventory/authoring tool call."""
    if not isinstance(payload, dict):
        raise NativeToolError("Native tool payload must be an object")

    action = payload.get("action")
    if not isinstance(action, str) or not action.strip():
        raise NativeToolError("Native tool payload must contain a non-empty 'action' field")
    action = action.strip()

    if tool_name == "ha_inventory_query":
        result = await _async_inventory_query(hass, action=action, payload=payload)
        return {
            "action": action,
            "resource_type": "inventory",
            "target_id": str(payload.get("id")) if payload.get("id") else None,
            "result": result,
        }

    resource_type = {
        "ha_automation_manage": RESOURCE_AUTOMATION,
        "ha_scene_manage": RESOURCE_SCENE,
        "ha_script_manage": RESOURCE_SCRIPT,
        "ha_blueprint_manage": RESOURCE_BLUEPRINT,
    }[tool_name]
    result = await _async_manage_resource(hass, resource_type=resource_type, action=action, payload=payload)
    return {
        "action": action,
        "resource_type": resource_type,
        "target_id": result.get("id") if isinstance(result, dict) else _target_id_from_payload(tool_name, payload),
        "result": result,
    }


async def _async_inventory_query(hass: Any, *, action: str, payload: dict[str, Any]) -> Any:
    """Handle read-only capability and inventory queries."""
    if action == "list_capabilities":
        return build_capabilities_payload(hass, enabled=True)

    if action == "list_entities":
        limit = _coerce_positive_int(payload.get("limit"))
        domain = payload.get("domain")
        search = payload.get("search")
        return {"items": _list_entities(hass, domain=domain, search=search, limit=limit)}

    if action in RESOURCE_LIST_ACTIONS.values():
        resource_type = next(key for key, value in RESOURCE_LIST_ACTIONS.items() if value == action)
        inventories = _build_inventories(hass)
        return {"items": inventories[resource_type]}

    if action in RESOURCE_GET_ACTIONS.values():
        resource_type = next(key for key, value in RESOURCE_GET_ACTIONS.items() if value == action)
        target_id = payload.get("id") or payload.get("target_id")
        if not isinstance(target_id, str) or not target_id.strip():
            raise NativeToolError("Inventory get actions require an 'id'")
        return _get_resource_definition(hass, resource_type=resource_type, target_id=target_id.strip())

    raise NativeToolError(f"Unsupported inventory action '{action}'")


async def _async_manage_resource(
    hass: Any,
    *,
    resource_type: str,
    action: str,
    payload: dict[str, Any],
) -> Any:
    """Handle CRUD and enable/disable actions for a native resource type."""
    if action == "list":
        return {"items": _build_inventories(hass)[resource_type]}

    if action == "get":
        target_id = _require_target_id(payload)
        return _get_resource_definition(hass, resource_type=resource_type, target_id=target_id)

    if resource_type == RESOURCE_BLUEPRINT:
        return await _async_manage_blueprint(hass, action=action, payload=payload)

    return await _async_manage_editable_yaml_resource(
        hass,
        resource_type=resource_type,
        action=action,
        payload=payload,
    )


async def _async_manage_editable_yaml_resource(
    hass: Any,
    *,
    resource_type: str,
    action: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Manage automations, scenes, and scripts via their default editable YAML files."""
    file_path = _resource_file_path(hass, resource_type)
    existing_data = _load_resource_file(resource_type, file_path)

    if action == "create":
        definition = payload.get("definition")
        if not isinstance(definition, dict):
            raise NativeToolError("Create requires a 'definition' object")
        prepared_id, prepared_definition = _prepare_create_definition(
            resource_type=resource_type,
            payload=payload,
            definition=definition,
        )
        current = _find_resource_record(existing_data, resource_type=resource_type, target_id=prepared_id)
        if current is not None:
            raise NativeToolError(f"{resource_type} '{prepared_id}' already exists")
        new_data = _add_resource_record(
            existing_data,
            resource_type=resource_type,
            target_id=prepared_id,
            definition=prepared_definition,
        )
        await _write_and_reload_resource_file(
            hass,
            resource_type=resource_type,
            file_path=file_path,
            old_data=existing_data,
            new_data=new_data,
        )
        return _get_resource_definition(hass, resource_type=resource_type, target_id=prepared_id)

    if action in {"update", "replace", "delete", "enable", "disable"}:
        target_id = _require_target_id(payload)
        record = _find_resource_record(existing_data, resource_type=resource_type, target_id=target_id)
        if record is None:
            if _resource_exists_read_only(hass, resource_type=resource_type, target_id=target_id):
                raise NativeToolError(
                    f"{resource_type} '{target_id}' exists but is not editable through the default Home Assistant managed file"
                )
            raise NativeToolError(f"{resource_type} '{target_id}' was not found")

        if action == "delete":
            new_data = _delete_resource_record(existing_data, resource_type=resource_type, target_id=target_id)
            await _write_and_reload_resource_file(
                hass,
                resource_type=resource_type,
                file_path=file_path,
                old_data=existing_data,
                new_data=new_data,
            )
            return {"id": target_id, "deleted": True}

        if action in {"enable", "disable"} and resource_type != RESOURCE_AUTOMATION:
            raise NativeToolError(f"'{action}' is only supported for automations")

        current_definition = deepcopy(record["definition"])
        if action == "replace":
            definition = payload.get("definition")
            if not isinstance(definition, dict):
                raise NativeToolError("Replace requires a 'definition' object")
            next_definition = _normalize_replaced_definition(
                resource_type=resource_type,
                target_id=target_id,
                definition=definition,
            )
        elif action == "update":
            patch = payload.get("patch")
            if not isinstance(patch, dict):
                raise NativeToolError("Update requires a 'patch' object")
            next_definition = _deep_merge(current_definition, patch)
            next_definition = _normalize_existing_definition(
                resource_type=resource_type,
                target_id=target_id,
                definition=next_definition,
            )
        else:
            next_definition = deepcopy(current_definition)
            next_definition["initial_state"] = action == "enable"

        new_data = _update_resource_record(
            existing_data,
            resource_type=resource_type,
            target_id=target_id,
            definition=next_definition,
        )
        await _write_and_reload_resource_file(
            hass,
            resource_type=resource_type,
            file_path=file_path,
            old_data=existing_data,
            new_data=new_data,
        )
        return _get_resource_definition(hass, resource_type=resource_type, target_id=target_id)

    raise NativeToolError(f"Unsupported {resource_type} action '{action}'")


async def _async_manage_blueprint(hass: Any, *, action: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Manage blueprint YAML files under the Home Assistant config directory."""
    if action == "list":
        return {"items": _build_blueprint_inventory(hass)}

    if action == "get":
        target_id = _require_target_id(payload)
        return _get_blueprint_definition(hass, target_id)

    if action == "create":
        definition = payload.get("definition")
        domain = payload.get("domain")
        if not isinstance(domain, str) or not domain.strip():
            raise NativeToolError("Blueprint create requires a 'domain'")
        if not isinstance(definition, dict):
            raise NativeToolError("Blueprint create requires a 'definition' object")
        target_id = _normalize_blueprint_target_id(payload.get("id"), domain, definition)
        file_path = _blueprint_file_path(hass, target_id)
        if file_path.exists():
            raise NativeToolError(f"blueprint '{target_id}' already exists")
        await _write_and_reload_blueprint(
            hass,
            file_path=file_path,
            definition=definition,
            blueprint_domain=domain,
        )
        return _get_blueprint_definition(hass, target_id)

    if action in {"update", "replace", "delete"}:
        target_id = _require_target_id(payload)
        existing = _get_blueprint_definition(hass, target_id)
        file_path = _blueprint_file_path(hass, target_id)

        if action == "delete":
            await _delete_and_reload_blueprint(
                hass,
                file_path=file_path,
                blueprint_domain=str(existing.get("domain") or "automation"),
            )
            return {"id": target_id, "deleted": True}

        if action == "replace":
            definition = payload.get("definition")
            if not isinstance(definition, dict):
                raise NativeToolError("Blueprint replace requires a 'definition' object")
            next_definition = deepcopy(definition)
        else:
            patch = payload.get("patch")
            if not isinstance(patch, dict):
                raise NativeToolError("Blueprint update requires a 'patch' object")
            next_definition = _deep_merge(deepcopy(existing["definition"]), patch)

        blueprint_block = next_definition.get("blueprint")
        if not isinstance(blueprint_block, dict) or not blueprint_block.get("domain"):
            raise NativeToolError("Blueprint definition must contain blueprint.domain")

        await _write_and_reload_blueprint(
            hass,
            file_path=file_path,
            definition=next_definition,
            blueprint_domain=str(blueprint_block["domain"]),
        )
        return _get_blueprint_definition(hass, target_id)

    raise NativeToolError(f"Unsupported blueprint action '{action}'")


def _build_inventories(hass: Any, *, entity_limit: int = 500) -> dict[str, list[dict[str, Any]]]:
    """Build inventories for entities and native resource types."""
    return {
        "entities": _list_entities(hass, limit=entity_limit),
        RESOURCE_AUTOMATION: _build_yaml_resource_inventory(hass, resource_type=RESOURCE_AUTOMATION),
        RESOURCE_SCENE: _build_yaml_resource_inventory(hass, resource_type=RESOURCE_SCENE),
        RESOURCE_SCRIPT: _build_yaml_resource_inventory(hass, resource_type=RESOURCE_SCRIPT),
        RESOURCE_BLUEPRINT: _build_blueprint_inventory(hass),
    }


def _list_entities(
    hass: Any,
    *,
    domain: str | None = None,
    search: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Return a simple entity inventory from the current HA state machine."""
    needle = search.lower().strip() if isinstance(search, str) and search.strip() else None
    items: list[dict[str, Any]] = []
    for state in sorted(hass.states.async_all(), key=lambda item: item.entity_id):
        entity_domain = getattr(state, "domain", None) or state.entity_id.split(".", 1)[0]
        if domain and entity_domain != domain:
            continue
        name = getattr(state, "name", None) or state.entity_id
        if needle and needle not in state.entity_id.lower() and needle not in str(name).lower():
            continue
        items.append(
            {
                "id": state.entity_id,
                "entity_id": state.entity_id,
                "name": name,
                "domain": entity_domain,
                "state": state.state,
            }
        )
        if limit is not None and len(items) >= limit:
            break
    return items


def _build_yaml_resource_inventory(hass: Any, *, resource_type: str) -> list[dict[str, Any]]:
    """Return inventory for automations, scenes, or scripts."""
    file_path = _resource_file_path(hass, resource_type)
    data = _load_resource_file(resource_type, file_path)
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    if resource_type in {RESOURCE_AUTOMATION, RESOURCE_SCENE}:
        for item in data:
            if not isinstance(item, dict):
                continue
            summary = _resource_summary_from_definition(resource_type, item, editable=True, source="managed_file")
            items.append(summary)
            seen.update(_resource_identifiers(resource_type, summary["id"], item))
    else:
        for object_id, item in data.items():
            if not isinstance(item, dict):
                continue
            summary = _resource_summary_from_definition(
                resource_type,
                item,
                editable=True,
                source="managed_file",
                target_id=object_id,
            )
            items.append(summary)
            seen.update(
                _resource_identifiers(
                    resource_type,
                    summary["id"],
                    item,
                    target_id_override=object_id,
                )
            )

    domain_entities = [
        state
        for state in hass.states.async_all()
        if getattr(state, "domain", state.entity_id.split(".", 1)[0]) == resource_type
    ]
    for state in sorted(domain_entities, key=lambda item: item.entity_id):
        entity_id = state.entity_id
        if entity_id in seen:
            continue
        items.append(
            {
                "id": entity_id,
                "entity_id": entity_id,
                "name": getattr(state, "name", None) or entity_id,
                "editable": False,
                "source": "state_only",
                "enabled": _state_enabled(state),
            }
        )

    return items


def _build_blueprint_inventory(hass: Any) -> list[dict[str, Any]]:
    """Return inventory for blueprint files."""
    root = _blueprint_root(hass)
    if not root.exists():
        return []

    items: list[dict[str, Any]] = []
    for file_path in sorted(root.rglob("*.yaml")):
        relative = file_path.relative_to(root).as_posix()
        definition = _load_yaml_file(file_path)
        blueprint_block = definition.get("blueprint") if isinstance(definition, dict) else None
        name = relative
        domain = None
        if isinstance(blueprint_block, dict):
            name = str(blueprint_block.get("name") or relative)
            domain = blueprint_block.get("domain")
        items.append(
            {
                "id": relative[:-5] if relative.endswith(".yaml") else relative,
                "path": relative,
                "name": name,
                "domain": domain,
                "editable": True,
                "source": "blueprints_directory",
                "enabled": None,
            }
        )
    return items


def _get_resource_definition(hass: Any, *, resource_type: str, target_id: str) -> dict[str, Any]:
    """Return the full resource definition or a read-only snapshot."""
    if resource_type == RESOURCE_BLUEPRINT:
        return _get_blueprint_definition(hass, target_id)

    file_path = _resource_file_path(hass, resource_type)
    data = _load_resource_file(resource_type, file_path)
    record = _find_resource_record(data, resource_type=resource_type, target_id=target_id)
    if record is not None:
        summary = _resource_summary_from_definition(
            resource_type,
            record["definition"],
            editable=True,
            source="managed_file",
            target_id=record["id"],
        )
        summary["definition"] = deepcopy(record["definition"])
        return summary

    for state in hass.states.async_all():
        if state.entity_id == target_id:
            return {
                "id": target_id,
                "entity_id": target_id,
                "name": getattr(state, "name", None) or target_id,
                "editable": False,
                "source": "state_only",
                "enabled": _state_enabled(state),
                "definition": {
                    "state": state.state,
                    "attributes": dict(getattr(state, "attributes", {}) or {}),
                },
            }
    raise NativeToolError(f"{resource_type} '{target_id}' was not found")


def _get_blueprint_definition(hass: Any, target_id: str) -> dict[str, Any]:
    """Return a blueprint definition from disk."""
    file_path = _blueprint_file_path(hass, target_id)
    if not file_path.exists():
        raise NativeToolError(f"blueprint '{target_id}' was not found")

    definition = _load_yaml_file(file_path)
    if not isinstance(definition, dict):
        raise NativeToolError(f"blueprint '{target_id}' contains invalid YAML")

    blueprint_block = definition.get("blueprint")
    name = target_id
    domain = None
    if isinstance(blueprint_block, dict):
        name = str(blueprint_block.get("name") or target_id)
        domain = blueprint_block.get("domain")

    relative = file_path.relative_to(_blueprint_root(hass)).as_posix()
    return {
        "id": relative[:-5] if relative.endswith(".yaml") else relative,
        "path": relative,
        "name": name,
        "domain": domain,
        "editable": True,
        "source": "blueprints_directory",
        "enabled": None,
        "definition": definition,
    }


def _resource_file_path(hass: Any, resource_type: str) -> Path:
    """Return the default editable YAML file for a resource type."""
    return Path(hass.config.path(EDITABLE_RESOURCE_FILES[resource_type]))


def _blueprint_root(hass: Any) -> Path:
    """Return the Home Assistant blueprint root directory."""
    return Path(hass.config.path("blueprints"))


def _blueprint_file_path(hass: Any, target_id: str) -> Path:
    """Resolve a blueprint identifier to a file path."""
    normalized = target_id.strip().replace("\\", "/")
    if normalized.endswith(".yaml"):
        normalized = normalized[:-5]
    parts = [part for part in normalized.split("/") if part and part != "."]
    if len(parts) < 2:
        raise NativeToolError("Blueprint ids must include domain and file path, for example 'automation/my_blueprint'")
    return _blueprint_root(hass).joinpath(*parts).with_suffix(".yaml")


def _load_resource_file(resource_type: str, file_path: Path) -> list[dict[str, Any]] | dict[str, dict[str, Any]]:
    """Load the editable YAML file for a resource type."""
    if not file_path.exists():
        return {} if resource_type == RESOURCE_SCRIPT else []
    loaded = _load_yaml_file(file_path)
    if loaded is None:
        return {} if resource_type == RESOURCE_SCRIPT else []
    if resource_type == RESOURCE_SCRIPT:
        if not isinstance(loaded, dict):
            raise NativeToolError(f"{file_path.name} must contain a mapping")
        return loaded
    if not isinstance(loaded, list):
        raise NativeToolError(f"{file_path.name} must contain a list")
    return loaded


def _load_yaml_file(file_path: Path) -> Any:
    """Load YAML content from a file path."""
    try:
        return yaml.safe_load(file_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except yaml.YAMLError as err:
        raise NativeToolError(f"Invalid YAML in {file_path.name}: {err}") from err


def _dump_yaml(data: Any) -> str:
    """Serialize YAML in a stable, readable form."""
    return yaml.safe_dump(
        data,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    )


async def _write_and_reload_resource_file(
    hass: Any,
    *,
    resource_type: str,
    file_path: Path,
    old_data: Any,
    new_data: Any,
) -> None:
    """Persist a resource file and roll it back if reload fails."""
    previous_exists = file_path.exists()
    previous_text = file_path.read_text(encoding="utf-8") if previous_exists else ""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(_dump_yaml(new_data), encoding="utf-8")

    try:
        await hass.services.async_call(resource_type, "reload", {}, blocking=True)
    except Exception as err:  # noqa: BLE001
        if previous_exists:
            file_path.write_text(previous_text, encoding="utf-8")
        else:
            file_path.unlink(missing_ok=True)
        try:
            await hass.services.async_call(resource_type, "reload", {}, blocking=True)
        except Exception:  # noqa: BLE001
            pass
        raise NativeToolError(
            f"Failed to reload {resource_type} after updating {file_path.name}: {err}"
        ) from err


async def _write_and_reload_blueprint(
    hass: Any,
    *,
    file_path: Path,
    definition: dict[str, Any],
    blueprint_domain: str,
) -> None:
    """Persist a blueprint and reload the owning domain."""
    previous_exists = file_path.exists()
    previous_text = file_path.read_text(encoding="utf-8") if previous_exists else ""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(_dump_yaml(definition), encoding="utf-8")

    try:
        await _reload_blueprint_owner(hass, blueprint_domain)
    except Exception as err:  # noqa: BLE001
        if previous_exists:
            file_path.write_text(previous_text, encoding="utf-8")
        else:
            file_path.unlink(missing_ok=True)
        try:
            await _reload_blueprint_owner(hass, blueprint_domain)
        except Exception:  # noqa: BLE001
            pass
        raise NativeToolError(
            f"Failed to reload {blueprint_domain} after updating blueprint '{file_path.name}': {err}"
        ) from err


async def _delete_and_reload_blueprint(hass: Any, *, file_path: Path, blueprint_domain: str) -> None:
    """Delete a blueprint file and reload its owner domain."""
    if not file_path.exists():
        raise NativeToolError(f"blueprint '{file_path.stem}' was not found")
    previous_text = file_path.read_text(encoding="utf-8")
    file_path.unlink()
    try:
        await _reload_blueprint_owner(hass, blueprint_domain)
    except Exception as err:  # noqa: BLE001
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(previous_text, encoding="utf-8")
        try:
            await _reload_blueprint_owner(hass, blueprint_domain)
        except Exception:  # noqa: BLE001
            pass
        raise NativeToolError(
            f"Failed to reload {blueprint_domain} after deleting blueprint '{file_path.name}': {err}"
        ) from err


async def _reload_blueprint_owner(hass: Any, blueprint_domain: str) -> None:
    """Reload the owning integration for a blueprint definition."""
    normalized = blueprint_domain.strip()
    if normalized in {"automation", "script", "scene"}:
        await hass.services.async_call(normalized, "reload", {}, blocking=True)


def _find_resource_record(data: Any, *, resource_type: str, target_id: str) -> dict[str, Any] | None:
    """Find a resource record inside the editable YAML data."""
    normalized_target = target_id.strip()
    if resource_type in {RESOURCE_AUTOMATION, RESOURCE_SCENE}:
        for index, item in enumerate(data):
            if not isinstance(item, dict):
                continue
            identifiers = _resource_identifiers(resource_type, None, item)
            if normalized_target in identifiers:
                resolved_id = _resource_primary_id(resource_type, item)
                return {"index": index, "id": resolved_id, "definition": item}
        return None

    for object_id, item in data.items():
        if not isinstance(item, dict):
            continue
        identifiers = _resource_identifiers(resource_type, object_id, item)
        if normalized_target in identifiers:
            return {"index": object_id, "id": object_id, "definition": item}
    return None


def _resource_exists_read_only(hass: Any, *, resource_type: str, target_id: str) -> bool:
    """Return whether a resource exists in HA but not in the editable default file."""
    target = target_id.strip()
    for item in _build_yaml_resource_inventory(hass, resource_type=resource_type):
        if item["id"] == target or item.get("entity_id") == target:
            return not bool(item.get("editable"))
    return False


def _add_resource_record(data: Any, *, resource_type: str, target_id: str, definition: dict[str, Any]) -> Any:
    """Insert a new resource definition into editable YAML data."""
    if resource_type in {RESOURCE_AUTOMATION, RESOURCE_SCENE}:
        next_data = list(data)
        next_data.append(definition)
        return next_data
    next_data = dict(data)
    next_data[target_id] = definition
    return next_data


def _update_resource_record(data: Any, *, resource_type: str, target_id: str, definition: dict[str, Any]) -> Any:
    """Replace an existing resource definition in editable YAML data."""
    record = _find_resource_record(data, resource_type=resource_type, target_id=target_id)
    if record is None:
        raise NativeToolError(f"{resource_type} '{target_id}' was not found")

    if resource_type in {RESOURCE_AUTOMATION, RESOURCE_SCENE}:
        next_data = list(data)
        next_data[record["index"]] = definition
        return next_data

    next_data = dict(data)
    next_data[record["index"]] = definition
    return next_data


def _delete_resource_record(data: Any, *, resource_type: str, target_id: str) -> Any:
    """Delete an existing resource definition from editable YAML data."""
    record = _find_resource_record(data, resource_type=resource_type, target_id=target_id)
    if record is None:
        raise NativeToolError(f"{resource_type} '{target_id}' was not found")

    if resource_type in {RESOURCE_AUTOMATION, RESOURCE_SCENE}:
        next_data = list(data)
        del next_data[record["index"]]
        return next_data

    next_data = dict(data)
    del next_data[record["index"]]
    return next_data


def _prepare_create_definition(
    *,
    resource_type: str,
    payload: dict[str, Any],
    definition: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    """Normalize a create payload into a resource id and persisted definition."""
    prepared = deepcopy(definition)
    explicit_id = payload.get("id")

    if resource_type == RESOURCE_AUTOMATION:
        alias = str(prepared.get("alias") or "").strip()
        if not alias:
            raise NativeToolError("Automation definitions must include 'alias'")
        target_id = _coerce_identifier(explicit_id or prepared.get("id") or alias)
        prepared["id"] = target_id
        return target_id, prepared

    if resource_type == RESOURCE_SCENE:
        name = str(prepared.get("name") or "").strip()
        if not name:
            raise NativeToolError("Scene definitions must include 'name'")
        target_id = _coerce_identifier(explicit_id or prepared.get("id") or name)
        prepared["id"] = target_id
        return target_id, prepared

    alias = str(prepared.get("alias") or "").strip()
    target_id = _coerce_identifier(explicit_id or payload.get("object_id") or alias or "script")
    if alias:
        prepared["alias"] = alias
    return target_id, prepared


def _normalize_existing_definition(
    *,
    resource_type: str,
    target_id: str,
    definition: dict[str, Any],
) -> dict[str, Any]:
    """Ensure preserved identity fields remain present after patch updates."""
    normalized = deepcopy(definition)
    if resource_type in {RESOURCE_AUTOMATION, RESOURCE_SCENE}:
        normalized["id"] = target_id
    return normalized


def _normalize_replaced_definition(
    *,
    resource_type: str,
    target_id: str,
    definition: dict[str, Any],
) -> dict[str, Any]:
    """Normalize identity and required fields during replace."""
    normalized = deepcopy(definition)
    if resource_type == RESOURCE_AUTOMATION:
        alias = str(normalized.get("alias") or "").strip()
        if not alias:
            raise NativeToolError("Automation definitions must include 'alias'")
        normalized["id"] = target_id
    elif resource_type == RESOURCE_SCENE:
        name = str(normalized.get("name") or "").strip()
        if not name:
            raise NativeToolError("Scene definitions must include 'name'")
        normalized["id"] = target_id
    return normalized


def _resource_summary_from_definition(
    resource_type: str,
    definition: dict[str, Any],
    *,
    editable: bool,
    source: str,
    target_id: str | None = None,
) -> dict[str, Any]:
    """Build an inventory summary from a persisted resource definition."""
    resource_id = target_id or _resource_primary_id(resource_type, definition)
    name = _resource_display_name(resource_type, definition, target_id=resource_id)
    summary = {
        "id": resource_id,
        "name": name,
        "editable": editable,
        "source": source,
        "enabled": _resource_enabled(resource_type, definition),
    }
    entity_id = _resource_entity_id(resource_type, definition, target_id=resource_id)
    if entity_id:
        summary["entity_id"] = entity_id
    return summary


def _resource_primary_id(resource_type: str, definition: dict[str, Any]) -> str:
    """Return the primary identifier for a resource definition."""
    if resource_type == RESOURCE_AUTOMATION:
        return _coerce_identifier(definition.get("id") or definition.get("alias") or "automation")
    if resource_type == RESOURCE_SCENE:
        return _coerce_identifier(definition.get("id") or definition.get("name") or "scene")
    return _coerce_identifier(definition.get("alias") or "script")


def _resource_display_name(resource_type: str, definition: dict[str, Any], *, target_id: str | None = None) -> str:
    """Return a display name for a resource definition."""
    if resource_type == RESOURCE_AUTOMATION:
        return str(definition.get("alias") or target_id or "automation")
    if resource_type == RESOURCE_SCENE:
        return str(definition.get("name") or target_id or "scene")
    return str(definition.get("alias") or target_id or "script")


def _resource_entity_id(resource_type: str, definition: dict[str, Any], *, target_id: str) -> str | None:
    """Guess the runtime entity_id for a persisted resource definition."""
    if resource_type == RESOURCE_AUTOMATION:
        return f"automation.{_coerce_identifier(definition.get('alias') or target_id)}"
    if resource_type == RESOURCE_SCENE:
        return f"scene.{_coerce_identifier(definition.get('id') or definition.get('name') or target_id)}"
    if resource_type == RESOURCE_SCRIPT:
        return f"script.{target_id}"
    return None


def _resource_identifiers(
    resource_type: str,
    target_id: str | None,
    definition: dict[str, Any],
    *,
    target_id_override: str | None = None,
) -> set[str]:
    """Return a set of identifiers that can address a resource."""
    resolved_id = target_id_override or target_id or _resource_primary_id(resource_type, definition)
    identifiers = {
        resolved_id,
        _resource_display_name(resource_type, definition, target_id=resolved_id),
    }
    entity_id = _resource_entity_id(resource_type, definition, target_id=resolved_id)
    if entity_id:
        identifiers.add(entity_id)
    explicit_id = definition.get("id")
    if isinstance(explicit_id, str) and explicit_id.strip():
        identifiers.add(explicit_id.strip())
    alias = definition.get("alias")
    if isinstance(alias, str) and alias.strip():
        identifiers.add(alias.strip())
    name = definition.get("name")
    if isinstance(name, str) and name.strip():
        identifiers.add(name.strip())
    return identifiers


def _resource_enabled(resource_type: str, definition: dict[str, Any]) -> bool | None:
    """Return enabled state metadata where the resource exposes one."""
    if resource_type != RESOURCE_AUTOMATION:
        return None
    initial_state = definition.get("initial_state")
    if isinstance(initial_state, bool):
        return initial_state
    if isinstance(initial_state, str):
        lowered = initial_state.lower().strip()
        if lowered in {"true", "on", "enabled"}:
            return True
        if lowered in {"false", "off", "disabled"}:
            return False
    return True


def _state_enabled(state: Any) -> bool | None:
    """Infer enabled state from an HA State object when possible."""
    if state.entity_id.startswith("automation."):
        return state.state != "off"
    return None


def _coerce_identifier(value: Any) -> str:
    """Convert a free-form label into a file- and entity-friendly identifier."""
    text = str(value or "").strip().lower()
    if not text:
        raise NativeToolError("Resource identifiers cannot be blank")
    output: list[str] = []
    last_was_sep = False
    for char in text:
        if char.isalnum():
            output.append(char)
            last_was_sep = False
            continue
        if char in {"_", "-", " "} and not last_was_sep:
            output.append("_")
            last_was_sep = True
    identifier = "".join(output).strip("_")
    if not identifier:
        raise NativeToolError(f"Could not derive a valid identifier from '{value}'")
    return identifier


def _deep_merge(existing: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge a patch into an existing mapping."""
    merged = deepcopy(existing)
    for key, value in patch.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = _deep_merge(current, value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _trim_items(items: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """Trim a list to a bounded length."""
    return items[: max(limit, 0)]


def _coerce_positive_int(value: Any) -> int | None:
    """Return a positive integer or None for invalid values."""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _require_target_id(payload: dict[str, Any]) -> str:
    """Return the requested target id or raise a tool error."""
    raw_value = payload.get("id") or payload.get("target_id")
    if not isinstance(raw_value, str) or not raw_value.strip():
        raise NativeToolError("This action requires an 'id'")
    return raw_value.strip()


def _normalize_blueprint_target_id(raw_id: Any, domain: str, definition: dict[str, Any]) -> str:
    """Build a normalized blueprint id from user input or metadata."""
    if isinstance(raw_id, str) and raw_id.strip():
        normalized = raw_id.strip().replace("\\", "/")
        if normalized.endswith(".yaml"):
            normalized = normalized[:-5]
        if "/" in normalized:
            return normalized
        return f"{domain.strip()}/{normalized}"

    blueprint_block = definition.get("blueprint")
    if not isinstance(blueprint_block, dict):
        raise NativeToolError("Blueprint definition must contain a 'blueprint' section")
    name = blueprint_block.get("name")
    if not isinstance(name, str) or not name.strip():
        raise NativeToolError("Blueprint definition must contain blueprint.name")
    return f"{domain.strip()}/{_coerce_identifier(name)}"


def _resource_type_for_tool(tool_name: str) -> str | None:
    """Return a resource type label for a tool name."""
    if tool_name in EXECUTE_SERVICE_TOOL_NAMES:
        return "service"
    return {
        "ha_inventory_query": "inventory",
        "ha_automation_manage": RESOURCE_AUTOMATION,
        "ha_scene_manage": RESOURCE_SCENE,
        "ha_script_manage": RESOURCE_SCRIPT,
        "ha_blueprint_manage": RESOURCE_BLUEPRINT,
    }.get(tool_name)


def _target_id_from_payload(tool_name: str, payload: Any) -> str | None:
    """Return a target id guess for telemetry payloads."""
    if not isinstance(payload, dict):
        return None
    raw_id = payload.get("id") or payload.get("target_id")
    if isinstance(raw_id, str) and raw_id.strip():
        return raw_id.strip()
    if tool_name == "ha_blueprint_manage":
        domain = payload.get("domain")
        definition = payload.get("definition")
        if isinstance(domain, str) and isinstance(definition, dict):
            try:
                return _normalize_blueprint_target_id(raw_id, domain, definition)
            except NativeToolError:
                return None
    return None
