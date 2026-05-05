/**
 * Normalize a model key so that provider:model and provider/model both match.
 * Returns all variants to try when looking up entries in a Record<string, …>.
 */
export function normalizeModelKey(key: string): string[] {
  if (!key) return []
  return [key, key.replace('/', ':'), key.replace(':', '/')]
}
