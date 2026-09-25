import { describe, expect, it } from 'vitest';

import { presentEntry } from '../../entries/presentation';
import { decodeEntry } from './entries';

const wire = {
  entry_id: 'entry-one',
  cursor: 7,
  actor_id: 'actor-lead',
  parent_actor_id: null,
  turn_id: null,
  occurred_at: 10,
  summary: null,
  type: 'extension',
  body: {
    owner: 'test.logs',
    entry_type: 'test.logs.line',
    source_event_id: 'event-one',
    schema_ref: {
      owner: 'test.logs',
      name: 'line',
      version: 1,
      digest: 'a'.repeat(64),
    },
    document: '{"text":"hello"}',
  },
};

describe('extension entries', () => {
  it('decodes a feature entry without its extension code', () => {
    const entry = decodeEntry(wire);

    expect(entry.type).toBe('extension');
    expect(entry.type === 'extension' && entry.body.owner).toBe('test.logs');
  });

  it('shows the summary, or the owner and entry type', () => {
    const plain = presentEntry(decodeEntry(wire), new Map());
    const summarized = presentEntry(
      decodeEntry({ ...wire, summary: 'One log line' }),
      new Map(),
    );

    expect(plain.kind === 'block' && plain.header).toEqual({
      kind: 'note',
      label: 'test.logs: test.logs.line',
    });
    expect(summarized.kind === 'block' && summarized.header).toEqual({
      kind: 'note',
      label: 'One log line',
    });
  });
});
