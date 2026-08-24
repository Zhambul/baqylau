import type { SessionId } from '../app/domain-ids';
import type { AttachmentReference } from '../controls/model';
import { resolveClipboardFiles, uploadAttachment } from '../api/attachments';

export type AttachmentItem = {
  readonly id: number;
  readonly name: string;
  readonly mediaType: string;
  readonly isImage: boolean;
  readonly thumbnailUrl: string;
  readonly localPath: string | null;
  readonly state: 'pending' | 'ready' | 'failed';
};

let nextItemId = 1;

export class AttachmentTrayState {
  items = $state<readonly AttachmentItem[]>([]);
  failure = $state<string | null>(null);

  constructor(initial: readonly AttachmentReference[] = []) {
    this.items = initial.map((attachment) => ({
      id: takeItemId(),
      name: attachment.displayName,
      mediaType: attachment.mediaType ?? 'application/octet-stream',
      isImage: attachment.mediaType?.startsWith('image/') === true,
      thumbnailUrl: '',
      localPath: attachment.localPath,
      state: 'ready',
    }));
  }

  get attachments(): readonly AttachmentReference[] {
    return this.items.flatMap((item) =>
      item.localPath === null
        ? []
        : [
            {
              localPath: item.localPath,
              displayName: item.name,
              mediaType: item.mediaType,
            },
          ],
    );
  }

  get pending(): boolean {
    return this.items.some((item) => item.state === 'pending');
  }

  async addFiles(
    files: ArrayLike<File> | readonly File[],
    sessionId: SessionId | null,
    uploadLimit: number | null,
  ): Promise<void> {
    this.failure = null;
    await Promise.all(
      Array.from(files, (file) => this.addFile(file, sessionId, uploadLimit)),
    );
  }

  async pasteFiles(
    files: readonly File[],
    sessionId: SessionId | null,
    uploadLimit: number | null,
    textarea: HTMLTextAreaElement,
  ): Promise<void> {
    this.failure = null;
    let paths: readonly string[] = [];
    try {
      paths = await resolveClipboardFiles(
        sessionId,
        files.map((file) => file.name),
      );
    } catch {
      // A clipboard probe can fail on a remote browser. Uploading preserves the
      // only path that the browser can complete in that case.
    }
    if (paths.length > 0) {
      insertAtCaret(textarea, paths.join(' '));
      return;
    }
    await this.addFiles(files, sessionId, uploadLimit);
  }

  remove(id: number): void {
    const item = this.items.find((candidate) => candidate.id === id);
    if (item !== undefined) revokeThumbnail(item.thumbnailUrl);
    this.items = this.items.filter((candidate) => candidate.id !== id);
  }

  clear(): void {
    for (const item of this.items) revokeThumbnail(item.thumbnailUrl);
    this.items = [];
    this.failure = null;
  }

  private async addFile(
    file: File,
    sessionId: SessionId | null,
    uploadLimit: number | null,
  ): Promise<void> {
    const name =
      file.name ||
      (file.type.startsWith('image/') ? 'screenshot.png' : 'attachment');
    if (file.size === 0) {
      this.failure = `${name} has no content to attach`;
      return;
    }
    if (uploadLimit === null) {
      this.failure = 'configuration is still loading; try the attachment again';
      return;
    }
    if (file.size > uploadLimit) {
      this.failure = `${name} exceeds the upload limit`;
      return;
    }

    const item: AttachmentItem = {
      id: takeItemId(),
      name,
      mediaType: file.type || 'application/octet-stream',
      isImage: file.type.startsWith('image/'),
      thumbnailUrl: thumbnail(file),
      localPath: null,
      state: 'pending',
    };
    this.items = [...this.items, item];

    try {
      const uploaded = await uploadAttachment(sessionId, file);
      this.replace(item.id, {
        ...item,
        name: uploaded.displayName,
        mediaType: uploaded.mediaType,
        isImage: uploaded.isImage,
        localPath: uploaded.localPath,
        state: 'ready',
      });
    } catch (error) {
      this.replace(item.id, { ...item, state: 'failed' });
      this.failure =
        error instanceof Error ? error.message : 'attachment upload failed';
    }
  }

  private replace(id: number, replacement: AttachmentItem): void {
    this.items = this.items.map((item) =>
      item.id === id ? replacement : item,
    );
  }
}

function takeItemId(): number {
  const id = nextItemId;
  nextItemId += 1;
  return id;
}

export function filesFromClipboard(event: ClipboardEvent): readonly File[] {
  const files: File[] = [];
  for (const item of event.clipboardData?.items ?? []) {
    if (item.kind !== 'file') continue;
    const file = item.getAsFile();
    if (file !== null) files.push(file);
  }
  return files;
}

export function hasDraggedFiles(event: DragEvent): boolean {
  return event.dataTransfer?.types.includes('Files') === true;
}

export function insertAtCaret(
  textarea: HTMLTextAreaElement,
  text: string,
): void {
  const start = textarea.selectionStart;
  const end = textarea.selectionEnd;
  const head = textarea.value.slice(0, start) + text;
  textarea.value = head + textarea.value.slice(end);
  textarea.setSelectionRange(head.length, head.length);
  textarea.focus();
  textarea.dispatchEvent(new Event('input', { bubbles: true }));
}

function thumbnail(file: File): string {
  if (!file.type.startsWith('image/')) return '';
  return typeof URL.createObjectURL === 'function'
    ? URL.createObjectURL(file)
    : '';
}

function revokeThumbnail(url: string): void {
  if (url.length > 0 && typeof URL.revokeObjectURL === 'function')
    URL.revokeObjectURL(url);
}
