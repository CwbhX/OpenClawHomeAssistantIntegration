from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
import yaml


def _load_native_tools_module():
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "custom_components" / "openclaw" / "native_tools.py"
    spec = importlib.util.spec_from_file_location("openclaw_native_tools", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


native_tools = _load_native_tools_module()


class FakeConfig:
    def __init__(self, root: Path) -> None:
        self._root = root

    def path(self, *parts: str) -> str:
        return str(self._root.joinpath(*parts))


class FakeServices:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict]] = []

    async def async_call(
        self,
        domain: str,
        service: str,
        data: dict | None = None,
        blocking: bool = False,
    ) -> None:
        self.calls.append((domain, service, dict(data or {})))


class FakeStates:
    def __init__(self, states: list[SimpleNamespace]) -> None:
        self._states = states

    def async_all(self) -> list[SimpleNamespace]:
        return list(self._states)


class FakeHass:
    def __init__(self, root: Path, states: list[SimpleNamespace] | None = None) -> None:
        self.config = FakeConfig(root)
        self.services = FakeServices()
        self.states = FakeStates(states or [])


def _state(entity_id: str, name: str, state: str, **attributes) -> SimpleNamespace:
    return SimpleNamespace(
        entity_id=entity_id,
        domain=entity_id.split(".", 1)[0],
        name=name,
        state=state,
        attributes=attributes,
    )


def _tool_call(name: str, arguments: dict) -> dict:
    return {
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {
                            "function": {
                                "name": name,
                                "arguments": json.dumps(arguments),
                            }
                        }
                    ]
                }
            }
        ]
    }


def test_build_capabilities_payload_includes_inventory(tmp_path: Path) -> None:
    (tmp_path / "automations.yaml").write_text(
        yaml.safe_dump(
            [
                {
                    "id": "morning_summary",
                    "alias": "Morning Summary",
                    "trigger": [],
                    "action": [],
                }
            ],
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (tmp_path / "scenes.yaml").write_text(
        yaml.safe_dump(
            [
                {
                    "id": "movie_time",
                    "name": "Movie Time",
                    "entities": {"light.tv_backlight": "on"},
                }
            ],
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (tmp_path / "scripts.yaml").write_text(
        yaml.safe_dump(
            {
                "open_blinds": {
                    "alias": "Open Blinds",
                    "sequence": [],
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    blueprint_dir = tmp_path / "blueprints" / "automation"
    blueprint_dir.mkdir(parents=True)
    (blueprint_dir / "arrive_home.yaml").write_text(
        yaml.safe_dump(
            {
                "blueprint": {
                    "name": "Arrive Home",
                    "domain": "automation",
                },
                "trigger": [],
                "action": [],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    hass = FakeHass(
        tmp_path,
        states=[
            _state("automation.morning_summary", "Morning Summary", "on"),
            _state("automation.external_only", "External Only", "on"),
            _state("light.kitchen", "Kitchen", "on"),
        ],
    )

    payload = native_tools.build_capabilities_payload(hass, enabled=True)

    assert payload["native_tools_enabled"] is True
    assert payload["counts"]["entities"] == 3
    assert payload["counts"]["automation"] == 2
    assert payload["counts"]["scene"] == 1
    assert payload["counts"]["script"] == 1
    assert payload["counts"]["blueprint"] == 1
    assert payload["inventory"]["automation"][0]["editable"] is True
    assert payload["inventory"]["automation"][1]["editable"] is False
    assert payload["inventory"]["blueprint"][0]["domain"] == "automation"


def test_build_capabilities_prompt_respects_disabled_flag(tmp_path: Path) -> None:
    hass = FakeHass(tmp_path)
    assert native_tools.build_capabilities_prompt(hass, enabled=False) is None


def test_native_automation_crud_and_telemetry(tmp_path: Path) -> None:
    hass = FakeHass(tmp_path)
    telemetry: list[native_tools.ToolExecutionResult] = []

    create_response = _tool_call(
        "ha_automation_manage",
        {
            "action": "create",
            "id": "night_check",
            "definition": {
                "alias": "Night Check",
                "trigger": [],
                "condition": [],
                "action": [],
            },
        },
    )

    results = asyncio.run(
        native_tools.async_execute_tool_calls(
            hass,
            create_response,
            service_tools_enabled=False,
            native_tools_enabled=True,
            record_execution=telemetry.append,
        )
    )

    assert results[0].ok is True
    assert results[0].resource_type == "automation"
    assert results[0].target_id == "night_check"
    assert telemetry[0].action == "create"
    saved = yaml.safe_load((tmp_path / "automations.yaml").read_text(encoding="utf-8"))
    assert saved[0]["id"] == "night_check"
    assert ("automation", "reload", {}) in hass.services.calls

    update_response = _tool_call(
        "ha_automation_manage",
        {
            "action": "update",
            "id": "night_check",
            "patch": {"initial_state": False},
        },
    )
    results = asyncio.run(
        native_tools.async_execute_tool_calls(
            hass,
            update_response,
            service_tools_enabled=False,
            native_tools_enabled=True,
            record_execution=telemetry.append,
        )
    )
    assert results[0].ok is True
    assert results[0].result["enabled"] is False

    delete_response = _tool_call(
        "ha_automation_manage",
        {"action": "delete", "id": "night_check"},
    )
    results = asyncio.run(
        native_tools.async_execute_tool_calls(
            hass,
            delete_response,
            service_tools_enabled=False,
            native_tools_enabled=True,
            record_execution=telemetry.append,
        )
    )
    assert results[0].result["deleted"] is True
    assert yaml.safe_load((tmp_path / "automations.yaml").read_text(encoding="utf-8")) == []


def test_read_only_state_only_resource_is_rejected(tmp_path: Path) -> None:
    hass = FakeHass(
        tmp_path,
        states=[_state("automation.read_only_rule", "Read Only Rule", "on")],
    )

    response = _tool_call(
        "ha_automation_manage",
        {
            "action": "update",
            "id": "automation.read_only_rule",
            "patch": {"initial_state": False},
        },
    )
    results = asyncio.run(
        native_tools.async_execute_tool_calls(
            hass,
            response,
            service_tools_enabled=False,
            native_tools_enabled=True,
        )
    )

    assert results[0].ok is False
    assert "not editable" in results[0].error


def test_blueprint_crud_and_reload(tmp_path: Path) -> None:
    hass = FakeHass(tmp_path)

    create_response = _tool_call(
        "ha_blueprint_manage",
        {
            "action": "create",
            "domain": "automation",
            "id": "automation/morning_routine",
            "definition": {
                "blueprint": {
                    "name": "Morning Routine",
                    "domain": "automation",
                },
                "trigger": [],
                "action": [],
            },
        },
    )
    results = asyncio.run(
        native_tools.async_execute_tool_calls(
            hass,
            create_response,
            service_tools_enabled=False,
            native_tools_enabled=True,
        )
    )
    assert results[0].ok is True
    assert results[0].target_id == "automation/morning_routine"
    assert (tmp_path / "blueprints" / "automation" / "morning_routine.yaml").exists()
    assert ("automation", "reload", {}) in hass.services.calls

    delete_response = _tool_call(
        "ha_blueprint_manage",
        {"action": "delete", "id": "automation/morning_routine"},
    )
    results = asyncio.run(
        native_tools.async_execute_tool_calls(
            hass,
            delete_response,
            service_tools_enabled=False,
            native_tools_enabled=True,
        )
    )
    assert results[0].result["deleted"] is True
    assert not (tmp_path / "blueprints" / "automation" / "morning_routine.yaml").exists()


def test_execute_service_tool_returns_structured_summary(tmp_path: Path) -> None:
    hass = FakeHass(tmp_path)
    response = _tool_call(
        "execute_services",
        {
            "list": [
                {
                    "domain": "light",
                    "service": "turn_on",
                    "service_data": {"entity_id": "light.kitchen"},
                }
            ]
        },
    )

    results = asyncio.run(
        native_tools.async_execute_tool_calls(
            hass,
            response,
            service_tools_enabled=True,
            native_tools_enabled=False,
        )
    )

    assert results[0].ok is True
    assert results[0].resource_type == "service"
    assert results[0].to_follow_up_summary()["tool"] == "execute_services"
    assert hass.services.calls == [("light", "turn_on", {"entity_id": "light.kitchen"})]
