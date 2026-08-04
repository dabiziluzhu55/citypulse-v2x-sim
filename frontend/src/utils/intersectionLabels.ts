export function formatIntersectionLabel(intersectionId: string): string {
  const match = /^demo_(\d+)$/.exec(intersectionId)
  return match ? `路口${match[1]}` : intersectionId
}
