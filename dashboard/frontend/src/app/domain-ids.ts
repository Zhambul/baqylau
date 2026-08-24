declare const identifierBrand: unique symbol;

type Identifier<Name extends string> = string & {
  readonly [identifierBrand]: Name;
};

export type ActorId = Identifier<'ActorId'>;
export type ClientId = Identifier<'ClientId'>;
export type DeviceId = Identifier<'DeviceId'>;
export type EntryId = Identifier<'EntryId'>;
export type RequestId = Identifier<'RequestId'>;
export type SessionId = Identifier<'SessionId'>;
export type TaskId = Identifier<'TaskId'>;

function identifier<Name extends string>(
  value: string,
  name: Name,
): Identifier<Name> {
  if (value.length === 0) {
    throw new Error(`${name} must not be empty`);
  }
  // This constructor is the only place where a checked string gets an opaque identity.
  // eslint-disable-next-line @typescript-eslint/no-unsafe-type-assertion
  return value as Identifier<Name>;
}

export function actorId(value: string): ActorId {
  return identifier(value, 'ActorId');
}

export function clientId(value: string): ClientId {
  return identifier(value, 'ClientId');
}

export function deviceId(value: string): DeviceId {
  return identifier(value, 'DeviceId');
}

export function entryId(value: string): EntryId {
  return identifier(value, 'EntryId');
}

export function requestId(value: string): RequestId {
  return identifier(value, 'RequestId');
}

export function sessionId(value: string): SessionId {
  return identifier(value, 'SessionId');
}

export function taskId(value: string): TaskId {
  return identifier(value, 'TaskId');
}
