import { beforeEach, describe, expect, it, vi } from 'vitest';

import { QUERY_CONCURRENCY, runExtensionQuery } from './extension-queries';

type PostOptions = {
  readonly params: { readonly path: { readonly extension_id: string } };
};

const { post } = vi.hoisted(() => ({
  post: vi.fn<(path: string, options: PostOptions) => Promise<unknown>>(),
}));

vi.mock('./client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./client')>()),
  apiClient: { POST: post },
}));

const READY = {
  status: 'ready',
  binding: {},
  document: { schema_ref: {}, json_text: '{}' },
  snapshot: { state_revision: 'one', cursor: null },
  next_page: null,
  content: null,
};

type Reply = (value: unknown) => void;

const replies: Reply[] = [];

function read(owner: string): Promise<unknown> {
  return runExtensionQuery(
    { owner, queryId: `${owner}.read`, scope: { kind: 'installation' } },
    '{}',
    new AbortController().signal,
  );
}

function started(owner: string): number {
  return post.mock.calls.filter(
    ([, options]) => options.params.path.extension_id === owner,
  ).length;
}

describe('extension queries', () => {
  beforeEach(() => {
    replies.length = 0;
    post.mockReset();
    post.mockImplementation(
      () =>
        new Promise((resolve) => {
          replies.push(resolve);
        }),
    );
  });

  it('runs a limited number of reads of one extension at a time', async () => {
    const reads = Array.from({ length: QUERY_CONCURRENCY + 2 }, () =>
      read('test.adapters'),
    );
    await vi.waitFor(() => {
      expect(started('test.adapters')).toBe(QUERY_CONCURRENCY);
    });

    replies[0]?.({ data: READY, response: new Response() });
    await reads[0];

    await vi.waitFor(() => {
      expect(started('test.adapters')).toBe(QUERY_CONCURRENCY + 1);
    });
  });

  it('does not hold the reads of one extension behind another', async () => {
    for (let index = 0; index < QUERY_CONCURRENCY; index += 1)
      void read('test.busy');
    void read('test.other');

    await vi.waitFor(() => {
      expect([started('test.busy'), started('test.other')]).toEqual([
        QUERY_CONCURRENCY,
        1,
      ]);
    });
  });
});
