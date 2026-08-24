import { beforeEach, describe, expect, it, vi } from 'vitest';

import { resolveClipboardFiles, uploadAttachment } from '../api/attachments';
import { sessionId } from '../app/domain-ids';
import { AttachmentTrayState, insertAtCaret } from './attachment-tray.svelte';

vi.mock('../api/attachments', () => ({
  resolveClipboardFiles: vi.fn(),
  uploadAttachment: vi.fn(),
}));

const resolveClipboardFilesMock = vi.mocked(resolveClipboardFiles);
const uploadAttachmentMock = vi.mocked(uploadAttachment);

describe('AttachmentTrayState', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('inserts host clipboard paths without uploading the pasted bytes', async () => {
    resolveClipboardFilesMock.mockResolvedValue(['/project/first.ts']);
    const tray = new AttachmentTrayState();
    const textarea = document.createElement('textarea');
    textarea.value = 'review  now';
    textarea.setSelectionRange(7, 7);

    await tray.pasteFiles(
      [new File(['source'], 'first.ts', { type: 'text/typescript' })],
      sessionId('session-1'),
      1_000,
      textarea,
    );

    expect(textarea.value).toBe('review /project/first.ts now');
    expect(textarea.selectionStart).toBe(24);
    expect(uploadAttachmentMock).not.toHaveBeenCalled();
    expect(tray.attachments).toHaveLength(0);
  });

  it('uploads pasted bytes when the clipboard does not resolve a path', async () => {
    resolveClipboardFilesMock.mockResolvedValue([]);
    uploadAttachmentMock.mockResolvedValue({
      localPath: '/uploads/session-1/image.png',
      displayName: 'image.png',
      mediaType: 'image/png',
      isImage: true,
    });
    const tray = new AttachmentTrayState();

    await tray.pasteFiles(
      [new File(['pixels'], 'image.png', { type: 'image/png' })],
      sessionId('session-1'),
      1_000,
      document.createElement('textarea'),
    );

    expect(uploadAttachmentMock).toHaveBeenCalledOnce();
    expect(tray.pending).toBe(false);
    expect(tray.attachments).toEqual([
      {
        localPath: '/uploads/session-1/image.png',
        displayName: 'image.png',
        mediaType: 'image/png',
      },
    ]);
  });

  it('falls back to upload when the clipboard probe fails', async () => {
    resolveClipboardFilesMock.mockRejectedValue(new Error('not on host'));
    uploadAttachmentMock.mockResolvedValue({
      localPath: '/uploads/staging/report.txt',
      displayName: 'report.txt',
      mediaType: 'text/plain',
      isImage: false,
    });
    const tray = new AttachmentTrayState();

    await tray.pasteFiles(
      [new File(['report'], 'report.txt', { type: 'text/plain' })],
      null,
      1_000,
      document.createElement('textarea'),
    );

    expect(uploadAttachmentMock).toHaveBeenCalledOnce();
    expect(tray.failure).toBeNull();
  });

  it('rejects empty and oversized files before upload', async () => {
    const tray = new AttachmentTrayState();

    await tray.addFiles([new File([], 'empty.py')], null, 5);
    expect(tray.failure).toBe('empty.py has no content to attach');

    await tray.addFiles([new File(['123456'], 'large.txt')], null, 5);
    expect(tray.failure).toBe('large.txt exceeds the upload limit');
    expect(uploadAttachmentMock).not.toHaveBeenCalled();
  });

  it('releases uploaded references when cleared', () => {
    const tray = new AttachmentTrayState([
      {
        localPath: '/uploads/staging/a.txt',
        displayName: 'a.txt',
        mediaType: 'text/plain',
      },
    ]);

    expect(tray.attachments).toHaveLength(1);
    tray.clear();
    expect(tray.attachments).toHaveLength(0);
  });
});

describe('insertAtCaret', () => {
  it('replaces the selected range and emits an input event', () => {
    const textarea = document.createElement('textarea');
    textarea.value = 'before old after';
    textarea.setSelectionRange(7, 10);
    const input = vi.fn();
    textarea.addEventListener('input', input);

    insertAtCaret(textarea, '/project/new');

    expect(textarea.value).toBe('before /project/new after');
    expect(textarea.selectionStart).toBe(19);
    expect(textarea.selectionEnd).toBe(19);
    expect(input).toHaveBeenCalledOnce();
  });
});
