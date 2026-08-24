export function isIPad(): boolean {
  const platform: unknown = Reflect.get(navigator, 'platform');
  return (
    navigator.userAgent.includes('iPad') ||
    (platform === 'MacIntel' && navigator.maxTouchPoints > 1)
  );
}
