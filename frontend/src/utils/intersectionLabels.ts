export function formatIntersectionLabel(intersectionId: string): string {
  const match = /^demo_(\d+)$/.exec(intersectionId)
  return match ? `demo${match[1]}` : intersectionId
}
