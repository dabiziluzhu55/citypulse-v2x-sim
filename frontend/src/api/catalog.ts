import { apiClient } from './client'
import type { CatalogResponse } from '../types/catalog'

export async function fetchCatalog(): Promise<CatalogResponse> {
  const { data } = await apiClient.get<CatalogResponse>('/catalog')
  return data
}
