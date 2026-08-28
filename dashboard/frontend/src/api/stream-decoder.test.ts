import { describe, expect, it } from 'vitest';

import { wireActor, wireEntry, wireSession } from '../test/session-fixture';
import {
  StreamValidationFailure,
  decodeGlobalApplicationFrame,
  decodeGlobalStreamFrame,
  decodeReadyFrame,
  decodeSessionApplicationFrame,
  decodeSessionStreamFrame,
} from './stream-decoder';

describe('stream decoder', () => {
  it('decodes the ready protocol and a complete global delta', () => {
    expect(decodeReadyFrame('{"boot_id":"boot-one"}')).toBe('boot-one');

    const frame = decodeGlobalStreamFrame(
      JSON.stringify({ sessions: [wireSession()], actors: [wireActor()] }),
    );

    expect(frame.sessions[0]?.sessionId).toBe('session-one');
    expect(frame.actors[0]?.actorId).toBe('actor-lead');
  });

  it('rejects malformed JSON and unknown discriminant values', () => {
    expect(() => decodeReadyFrame('not-json')).toThrow(StreamValidationFailure);
    expect(() =>
      decodeGlobalStreamFrame(
        JSON.stringify({
          sessions: [],
          actors: [{ ...wireActor(), status: 'surprised' }],
        }),
      ),
    ).toThrow(/unknown actor status/);
  });

  it('decodes session deltas and validates entry discriminants at runtime', () => {
    const frame = decodeSessionStreamFrame(
      JSON.stringify({
        session: wireSession(),
        actors: [wireActor()],
        entries: [wireEntry(43)],
      }),
    );

    expect(frame.session?.sessionId).toBe('session-one');
    expect(frame.entries[0]?.entryId).toBe('entry-43');

    expect(() =>
      decodeSessionStreamFrame(
        JSON.stringify({
          session: null,
          actors: [],
          entries: [{ ...wireEntry(44), type: 'unexpected' }],
        }),
      ),
    ).toThrow(/unknown type/);
  });

  it('decodes a global application snapshot and rejects an invalid usage row', () => {
    const wire = {
      usage_rows: [
        {
          harness: 'codex',
          account_id: null,
          display_name: 'Default',
          switchable: false,
          default_for_launch: true,
          plan: 'pro',
          windows: [
            {
              key: 'five-hour',
              label: '5 hour',
              used_percent: '25',
              resets_at: 1_700_001_000,
              duration_minutes: 300,
              scope: 'account',
              model_id: null,
            },
          ],
          scheduling_score: '75',
          scheduling_allowed: true,
          limit: null,
          authentication_error: null,
          collection_error: null,
        },
      ],
      notifications: { enabled: false, latest: null },
      preferences: {
        new_session: {
          working_directory: '/work',
          harness: 'codex',
          model: 'gpt-5.6-sol',
          effort: 'high',
        },
        new_session_drafts: [],
        hidden_directories: { '/old': 1_700_000_000 },
        limits: {
          upload_bytes: 1_000,
          rename_characters: 80,
          presence_seconds: 30,
        },
      },
    };

    const application = decodeGlobalApplicationFrame(JSON.stringify(wire));

    expect(application.notifications.enabled).toBe(false);
    expect(application.usageRows[0]?.windows[0]?.scope).toBe('account');
    expect(application.preferences.hiddenDirectories.get('/old')).toBe(
      1_700_000_000,
    );
    expect(() =>
      decodeGlobalApplicationFrame(
        JSON.stringify({
          ...wire,
          usage_rows: [{ ...wire.usage_rows[0], switchable: 'yes' }],
        }),
      ),
    ).toThrow(/switchable/);
  });

  it('decodes a terminal draft from a session application event', () => {
    const application = decodeSessionApplicationFrame(
      JSON.stringify({
        preferences: {
          view_mode: 'default',
          notifications_muted: false,
          tasks_hidden: false,
        },
        composer: {
          draft: { text: 'test', origin: 'terminal', sequence: 1000 },
          queue: null,
        },
        dialog: { draft: null },
        terminal: {
          window_id: 'window-one',
          input_state: { typed_text: 'test', suggestion: null },
        },
        errors: [],
      }),
    );

    expect(application.composer.draft?.text).toBe('test');
    expect(application.composer.draft?.origin).toBe('terminal');
    expect(() =>
      decodeSessionApplicationFrame(
        JSON.stringify({ preferences: { view_mode: 'unknown' } }),
      ),
    ).toThrow(StreamValidationFailure);
  });
});
