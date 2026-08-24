import type { SessionId } from '../app/domain-ids';
import { apiClient, execute } from './client';

export type UploadedAttachment = {
  readonly localPath: string;
  readonly displayName: string;
  readonly mediaType: string;
  readonly isImage: boolean;
};

export async function uploadAttachment(
  sessionId: SessionId | null,
  file: File,
): Promise<UploadedAttachment> {
  const data = await fileToBase64(file);
  const result = await execute(() =>
    apiClient.POST('/api/application/uploads', {
      body: {
        session_id: sessionId,
        name: file.name,
        mime: file.type || 'application/octet-stream',
        data,
      },
    }),
  );
  return {
    localPath: result.path,
    displayName: result.name,
    mediaType: result.mime,
    isImage: result.is_image,
  };
}

export async function resolveClipboardFiles(
  sessionId: SessionId | null,
  names: readonly string[],
): Promise<readonly string[]> {
  const result = await execute(() =>
    apiClient.POST('/api/application/clipboard-files', {
      body: { session_id: sessionId, names: [...names] },
    }),
  );
  return result.paths;
}

function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => {
      reject(reader.error ?? new Error('the file could not be read'));
    };
    reader.onload = () => {
      const value = typeof reader.result === 'string' ? reader.result : '';
      const separator = value.indexOf(',');
      resolve(separator >= 0 ? value.slice(separator + 1) : value);
    };
    reader.readAsDataURL(file);
  });
}
