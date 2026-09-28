import type {
  SettingsDocument,
  SettingsScope,
  SettingsSnapshot,
} from '../../api/extension-settings';

/** What an accepted change does; the host never rewrites recorded history. */
export const SETTINGS_EFFECT =
  'Views receive the new values now, and new processing uses them. ' +
  'Recorded history does not change. Reprocess a session to apply the new ' +
  'values to its past events.';

type SchemaRef = SettingsDocument['schema_ref'];

function sameReference(first: SchemaRef, second: SchemaRef): boolean {
  return (
    first.owner === second.owner &&
    first.name === second.name &&
    first.version === second.version &&
    first.digest === second.digest
  );
}

/** The JSON Schema of the scope's documents, from the package declaration. */
export function settingsSchema(
  snapshot: SettingsSnapshot,
): Record<string, unknown> | null {
  const definition = snapshot.schemas.find((schema) =>
    sameReference(schema.reference, snapshot.effective.schema_ref),
  );
  if (definition === undefined) return null;
  const parsed: unknown = JSON.parse(definition.json_text);
  return isObject(parsed) ? parsed : null;
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/** The values that the form starts from: the effective document of the scope. */
export function effectiveValue(snapshot: SettingsSnapshot): unknown {
  return JSON.parse(snapshot.effective.json_text);
}

/** Encode form values as a complete document in the scope's schema. */
export function settingsDocument(
  snapshot: SettingsSnapshot,
  value: unknown,
): SettingsDocument {
  return {
    schema_ref: snapshot.effective.schema_ref,
    json_text: JSON.stringify(value),
  };
}

export function scopeLabel(scope: SettingsScope): string {
  switch (scope.kind) {
    case 'installation':
      return 'All workspaces';
    case 'workspace':
      return `Workspace ${scope.workspace_id.slice(0, 12)}`;
    default:
      return scope.kind;
  }
}
