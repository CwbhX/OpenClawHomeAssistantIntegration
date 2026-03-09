# Native Tool Reference

This file defines the tool contracts expected by the updated Home Assistant integration.

## `ha_inventory_query`

### Actions

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

### Payloads

List entities:

```json
{
  "action": "list_entities",
  "domain": "light",
  "search": "kitchen",
  "limit": 20
}
```

Get automation:

```json
{
  "action": "get_automation",
  "id": "automation.morning_summary"
}
```

## `ha_automation_manage`

### Actions

- `list`
- `get`
- `create`
- `update`
- `replace`
- `delete`
- `enable`
- `disable`

### Notes

- Automations are editable when they live in the integration-managed default `automations.yaml`.
- `update` deep-merges the provided `patch`.
- `replace` overwrites the persisted definition.

## `ha_scene_manage`

### Actions

- `list`
- `get`
- `create`
- `update`
- `replace`
- `delete`

### Notes

- Scenes are editable when they live in the integration-managed default `scenes.yaml`.

## `ha_script_manage`

### Actions

- `list`
- `get`
- `create`
- `update`
- `replace`
- `delete`

### Notes

- Scripts are editable when they live in the integration-managed default `scripts.yaml`.

## `ha_blueprint_manage`

### Actions

- `list`
- `get`
- `create`
- `update`
- `replace`
- `delete`

### Notes

- Blueprints are stored as YAML files under `blueprints/<domain>/`.
- Blueprint `id` values are path-like, for example `automation/morning_routine`.
- Blueprint create requests should include `domain` and `definition`.

## Result Expectations

Tool results include structured metadata such as:

- `tool`
- `action`
- `resource_type`
- `target_id`
- `ok`
- `error`
- `duration_ms`
- `result_preview`

Base decisions and user-facing responses on these tool results rather than assuming a change succeeded.
