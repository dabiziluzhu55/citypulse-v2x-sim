import { apiClient } from './client.ts'
import type { EvaluationReportRequest } from '../utils/evaluationReport.ts'
import { filenameFromContentDisposition } from '../utils/evaluationReport.ts'

const EVALUATION_REPORT_TIMEOUT_MS = 30_000

export async function exportEvaluationReportPdf(
  payload: EvaluationReportRequest,
): Promise<{ blob: Blob; filename: string | null }> {
  const { data, headers } = await apiClient.postBlob('/evaluation-reports/pdf', payload, {
    timeoutMs: EVALUATION_REPORT_TIMEOUT_MS,
    accept: 'application/pdf',
  })
  return {
    blob: data,
    filename: filenameFromContentDisposition(headers.get('Content-Disposition')),
  }
}
