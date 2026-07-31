export const CROSSWALK_DEPTH_METERS = 4
export const CROSSWALK_SETBACK_METERS = 1.2
export const CROSSWALK_CENTER_OFFSET_METERS = CROSSWALK_SETBACK_METERS + CROSSWALK_DEPTH_METERS / 2
export const CROSSWALK_STRIPE_WIDTH_METERS = 0.5
export const CROSSWALK_STRIPE_GAP_METERS = 0.4
export const CROSSWALK_EDGE_INSET_METERS = 0.25

export function buildCrosswalkBarProjections(
  minimum: number,
  maximum: number,
  horizontalScale = 1,
): number[] {
  const barWidth = CROSSWALK_STRIPE_WIDTH_METERS * horizontalScale
  const pitch = (CROSSWALK_STRIPE_WIDTH_METERS + CROSSWALK_STRIPE_GAP_METERS) * horizontalScale
  const usableMinimum = minimum + CROSSWALK_EDGE_INSET_METERS * horizontalScale
  const usableMaximum = maximum - CROSSWALK_EDGE_INSET_METERS * horizontalScale
  const usableWidth = Math.max(barWidth, usableMaximum - usableMinimum)
  const count = Math.max(1, Math.floor(
    (usableWidth + CROSSWALK_STRIPE_GAP_METERS * horizontalScale) / pitch,
  ))
  const first = (usableMinimum + usableMaximum - (count - 1) * pitch) / 2
  return Array.from({ length: count }, (_, index) => first + index * pitch)
}
