# OpenClaw Home Assistant Integration Skill

Use this skill when OpenClaw is connected to Home Assistant through the `openclaw` custom integration and the integration advertises native Home Assistant management capabilities in the system prompt.

## Operating Rules

1. Prefer the integration's native Home Assistant tools over generic REST-style actions whenever they are available.
2. Use the native `ha_*` tools only through the normal conversation or message flow. Do not try to invoke `ha_inventory_query`, `ha_automation_manage`, `ha_scene_manage`, `ha_script_manage`, or `ha_blueprint_manage` through `openclaw.invoke_tool` or gateway `/tools/invoke`.
3. Use `ha_inventory_query` before creating or changing automations, scenes, scripts, or blueprints unless the capability manifest already contains enough detail.
4. Treat `editable: false` resources as read-only. Do not try to mutate them through the native tool surface.
5. Use `create` only for new resources. Use `update` for partial changes and `replace` only when you intend to overwrite the full definition.
6. For automations, use `enable` and `disable` instead of patching enable state manually.
7. Fall back to generic gateway tools or a separate Home Assistant skill only when the native tool surface cannot satisfy the request.

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
- If a request is about Home Assistant object authoring, do not reason your way into `openclaw.invoke_tool`. Stay on the native conversation path.

## Fast Path

For Home Assistant authoring requests, follow this exact order:

1. Read the capability manifest already present in conversation context.
2. If needed, use `ha_inventory_query` to inspect current resources.
3. Use the appropriate `ha_*_manage` tool.
4. Verify the result with `get` or the relevant inventory query.
5. Answer using the returned tool result.

## Never Do This

- Do not call `ha_*` tools through `openclaw.invoke_tool`.
- Do not treat a 404 from gateway `/tools/invoke` as proof that the native integration tools are unavailable.
- Do not recommend manual YAML or generic REST first when the native conversation path should work.

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
- Use `openclaw.invoke_tool` only for actual OpenClaw gateway tools that exist on `/tools/invoke`.
- Use another Home Assistant skill or generic REST only when the integration-native tools truly cannot satisfy the request.

## Troubleshooting

- If gateway `/tools/invoke` returns 404 for a native `ha_*` tool, that is expected and not a native tool failure.
- If a Home Assistant authoring request does not happen, the first question is whether the normal conversation path emitted a tool call, not whether `/tools/invoke` knows about that tool.
- If capability text is present but the model still avoids native tools, retry on the normal conversation path with an explicit instruction: use native Home Assistant integration tools only, do not use `openclaw.invoke_tool`.
