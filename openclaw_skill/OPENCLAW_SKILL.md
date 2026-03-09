# OpenClaw Home Assistant Integration Skill

Use this skill when OpenClaw is connected to Home Assistant through the `openclaw` custom integration and the integration advertises native Home Assistant management capabilities in the system prompt.

## Operating Rules

1. Prefer the integration's native Home Assistant tools over generic REST-style actions whenever they are available.
2. Use `ha_inventory_query` before creating or changing automations, scenes, scripts, or blueprints unless the capability manifest already contains enough detail.
3. Treat `editable: false` resources as read-only. Do not try to mutate them through the native tool surface.
4. Use `create` only for new resources. Use `update` for partial changes and `replace` only when you intend to overwrite the full definition.
5. For automations, use `enable` and `disable` instead of patching enable state manually.
6. Fall back to generic gateway tools or a separate Home Assistant skill only when the native tool surface cannot satisfy the request.

## Preferred Workflow

### Inventory and inspection

- Use `ha_inventory_query` with:
  - `list_capabilities`
  - `list_entities`
  - `list_automations`
  - `list_scenes`
  - `list_scripts`
  - `list_blueprints`
  - `get_automation`
  - `get_scene`
  - `get_script`
  - `get_blueprint`

### Authoring and lifecycle

- Use `ha_automation_manage` for automations.
- Use `ha_scene_manage` for scenes.
- Use `ha_script_manage` for scripts.
- Use `ha_blueprint_manage` for blueprints.

Supported actions:

- Automations: `list`, `get`, `create`, `update`, `replace`, `delete`, `enable`, `disable`
- Scenes: `list`, `get`, `create`, `update`, `replace`, `delete`
- Scripts: `list`, `get`, `create`, `update`, `replace`, `delete`
- Blueprints: `list`, `get`, `create`, `update`, `replace`, `delete`

## Authoring Guidance

- Pass Home Assistant native definitions as JSON objects.
- For `create`, send `definition`.
- For `update`, send `patch`.
- For `replace`, send `definition`.
- For `get`, `delete`, `enable`, and `disable`, send `id`.
- For blueprint creation, send both `domain` and `definition`. Include `id` when you want a specific file path.

## Decision Rules

- If the user asks "what is available", start with `list_capabilities` or the relevant `list_*` action.
- If the user asks to modify an existing object, inspect it first with `get`.
- If the user asks for a new automation, scene, script, or blueprint, check for duplicates before creating.
- If a native tool reports that a resource exists but is not editable, explain that it is outside the integration-managed editable surface and use a different path only if the user still wants that.
- When native tools succeed, base the user-facing answer on the structured tool result rather than guessing the outcome.

## Examples

Create a new automation:

```json
{
  "action": "create",
  "id": "nightly_house_check",
  "definition": {
    "alias": "Nightly House Check",
    "trigger": [
      {
        "platform": "time",
        "at": "23:00:00"
      }
    ],
    "condition": [],
    "action": [
      {
        "service": "notify.mobile_app_phone",
        "data": {
          "message": "House check complete."
        }
      }
    ]
  }
}
```

Patch an existing script:

```json
{
  "action": "update",
  "id": "script.open_blinds",
  "patch": {
    "sequence": [
      {
        "service": "cover.open_cover",
        "target": {
          "entity_id": "cover.living_room"
        }
      }
    ]
  }
}
```

Inspect a blueprint:

```json
{
  "action": "get_blueprint",
  "id": "automation/morning_routine"
}
```

## Fallbacks

- Use legacy `execute_service` or `execute_services` only for direct service execution requests that do not require native object authoring.
- Use `openclaw.invoke_tool` or another Home Assistant skill only when the integration-native tools cannot satisfy the request.
