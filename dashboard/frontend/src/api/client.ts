import createClient from 'openapi-fetch';

import type { paths } from './generated/schema';

export const apiClient = createClient<paths>({
  baseUrl: window.location.origin,
});

export type ApiResult<Data> = {
  readonly data?: Data;
  readonly error?: unknown;
  readonly response: Response;
};

export class HttpFailure extends Error {
  readonly kind = 'http';

  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = 'HttpFailure';
  }
}

class NetworkFailure extends Error {
  readonly kind = 'network';

  constructor(message: string, options?: ErrorOptions) {
    super(message, options);
    this.name = 'NetworkFailure';
  }
}

class RequestCancelled extends Error {
  readonly kind = 'cancelled';

  constructor() {
    super('request cancelled');
    this.name = 'RequestCancelled';
  }
}

export function messageFrom(value: unknown): string {
  if (typeof value === 'object' && value !== null && 'error' in value) {
    const message = Reflect.get(value, 'error');
    if (typeof message === 'string' && message.length > 0) {
      return message;
    }
  }
  return 'request failed';
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export async function execute<Data>(
  operation: () => Promise<ApiResult<Data>>,
): Promise<Data> {
  const result = await request(operation);
  if (result.data !== undefined) {
    return result.data;
  }
  throw new HttpFailure(result.response.status, messageFrom(result.error));
}

export async function request<Data>(
  operation: () => Promise<ApiResult<Data>>,
): Promise<ApiResult<Data>> {
  try {
    return await operation();
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new RequestCancelled();
    }
    throw new NetworkFailure(errorMessage(error), { cause: error });
  }
}
