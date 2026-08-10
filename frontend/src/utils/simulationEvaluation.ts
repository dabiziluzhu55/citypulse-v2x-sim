import type { SimulationEvaluation } from '../types/simulation'

export function simulationFuelIntensity(
  evaluation: Pick<SimulationEvaluation, 'fuel_consumption' | 'fuel_intensity_L_per_100km'>,
): number | null {
  if (typeof evaluation.fuel_consumption === 'number') return evaluation.fuel_consumption
  if (typeof evaluation.fuel_intensity_L_per_100km === 'number') {
    return evaluation.fuel_intensity_L_per_100km
  }
  return null
}
