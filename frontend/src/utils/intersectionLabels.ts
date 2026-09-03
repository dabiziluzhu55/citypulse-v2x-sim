export function formatIntersectionLabel(intersectionId: string): string {
  const match = /^demo_(\d+)$/.exec(intersectionId)
  return match ? `路口${match[1]}` : intersectionId
}

export function formatIntersectionReferences(value: unknown): string {
  return String(value ?? '').replace(
    /\bdemo_(\d+)\b/g,
    (_matched, number: string) => `路口${number}`,
  )
}
