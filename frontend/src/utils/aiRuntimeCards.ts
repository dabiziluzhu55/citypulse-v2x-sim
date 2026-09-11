import type { PredictionPayload } from '../types/intelligence'
import type { AIControlStatus } from '../types/simulation'
import { formatIntersectionLabel } from './intersectionLabels'

export type RuntimeCardTone = 'normal' | 'up' | 'down' | 'planning' | 'alert' | 'muted'

export interface RuntimeStatusCard {
  key: 'controlled' | 'prediction' | 'strategy' | 'nextDecision'
  label: string
  value: string
  title: string
  tone: RuntimeCardTone
  textValue?: boolean
}

function intersectionFromScope(activeScope: string | null | undefined): string | null {
  const normalized = String(activeScope || '').trim()
  const prefix = 'intersection:'
  if (!normalized.startsWith(prefix)) return null
  const intersectionId = normalized.slice(prefix.length).trim()
  return intersectionId || null
}

function predictionTargets(
  aiTakeover: AIControlStatus | null | undefined,
  activeScope: string | null | undefined,
): string[] {
  const controlled = aiTakeover?.controlled_intersections ?? []
  if (controlled.length > 0) return [...controlled]
  const scoped = intersectionFromScope(activeScope)
  return scoped ? [scoped] : []
}

function isPlanning(
  aiTakeover: AIControlStatus | null | undefined,
  simulationState: string | null | undefined,
): boolean {
  if (!aiTakeover?.ai_enabled) return false
  if (aiTakeover.state === 'ARMED') return true
  return aiTakeover.state === 'ACTIVE' && String(simulationState || '').toUpperCase() === 'PAUSED'
}

export function buildAiRuntimeCards(options: {
  aiTakeover?: AIControlStatus | null
  prediction?: PredictionPayload | null
  elapsedSeconds?: number | null
  simulationState?: string | null
  activeScope?: string | null
}): RuntimeStatusCard[] {
  const aiTakeover = options.aiTakeover ?? null
  const prediction = options.prediction ?? null
  const elapsedSeconds = options.elapsedSeconds
  const simulationState = options.simulationState ?? null
  const activeScope = options.activeScope ?? null
  const enabled = Boolean(aiTakeover?.ai_enabled)

  return [
    controlledIntersectionCard(aiTakeover, enabled),
    predictionCard(aiTakeover, prediction, activeScope),
    strategyCard(aiTakeover, enabled, isPlanning(aiTakeover, simulationState)),
    nextDecisionCard(aiTakeover, enabled, elapsedSeconds, isPlanning(aiTakeover, simulationState)),
  ]
}

function controlledIntersectionCard(
  aiTakeover: AIControlStatus | null,
  enabled: boolean,
): RuntimeStatusCard {
  const ids = enabled ? (aiTakeover?.controlled_intersections ?? []) : []
  if (!enabled || ids.length === 0) {
    return {
      key: 'controlled',
      label: '受控路口',
      value: '--',
      title: '当前没有受控路口',
      tone: 'muted',
    }
  }
  const names = ids.map((id) => formatIntersectionLabel(id))
  return {
    key: 'controlled',
    label: '受控路口',
    value: `${ids.length} 个`,
    title: names.join('、'),
    tone: 'normal',
  }
}

function predictionCard(
  aiTakeover: AIControlStatus | null,
  prediction: PredictionPayload | null | undefined,
  activeScope: string | null,
): RuntimeStatusCard {
  const targets = predictionTargets(aiTakeover, activeScope)
  if (targets.length === 0) {
    return {
      key: 'prediction',
      label: '60S交通流量预测',
      value: '--',
      title: '没有明确的预测目标路口',
      tone: 'muted',
    }
  }
  if (!prediction || prediction.ready !== true) {
    return {
      key: 'prediction',
      label: '60S交通流量预测',
      value: '预测未就绪',
      title: '预测尚未就绪',
      tone: 'muted',
      textValue: true,
    }
  }
  if (prediction.fallback === true) {
    return {
      key: 'prediction',
      label: '60S交通流量预测',
      value: '预测降级',
      title: prediction.fallback_reason || '当前使用降级预测',
      tone: 'alert',
      textValue: true,
    }
  }

  const rows = targets
    .map((id) => prediction.intersections?.[id])
    .filter((row): row is NonNullable<typeof row> => Boolean(row))
  if (rows.length === 0) {
    return {
      key: 'prediction',
      label: '60S交通流量预测',
      value: '--',
      title: '目标路口暂无预测结果',
      tone: 'muted',
    }
  }

  const current = rows.reduce((sum, row) => sum + Number(row.current_vehicle_count || 0), 0)
  const predicted = rows.reduce((sum, row) => sum + Number(row.predicted_vehicle_count || 0), 0)
  const delta = predicted - current
  const ratio = current > 1e-9
    ? (Number.isFinite(Number(rows[0]?.delta_ratio)) && rows.length === 1
      ? Number(rows[0].delta_ratio)
      : delta / current)
    : null
  const percent = ratio == null ? null : `${Math.abs(ratio * 100).toFixed(1)}%`
  const direction = delta > 1e-9 ? 'up' : delta < -1e-9 ? 'down' : 'normal'
  const arrow = direction === 'up' ? '↑' : direction === 'down' ? '↓' : ''
  return {
    key: 'prediction',
    label: '60S交通流量预测',
    value: percent == null ? `${delta >= 0 ? '+' : ''}${Math.round(delta)} 辆` : `${arrow}${percent}`,
    title: `当前${Math.round(current)}辆 → 预计${Math.round(predicted)}辆`,
    tone: direction === 'up' ? 'up' : direction === 'down' ? 'down' : 'normal',
  }
}

function strategyCard(
  aiTakeover: AIControlStatus | null,
  enabled: boolean,
  planning: boolean,
): RuntimeStatusCard {
  if (!enabled) {
    return {
      key: 'strategy',
      label: '当前AI策略',
      value: '--',
      title: 'AI管控未开启',
      tone: 'muted',
      textValue: true,
    }
  }
  if (planning) {
    return {
      key: 'strategy',
      label: '当前AI策略',
      value: '策略生成中',
      title: aiTakeover?.last_objective || '正在生成管控策略',
      tone: 'planning',
      textValue: true,
    }
  }
  if (aiTakeover?.state === 'FALLBACK') {
    return {
      key: 'strategy',
      label: '当前AI策略',
      value: '基线回退',
      title: aiTakeover.fallback_reason || aiTakeover.last_reason || '已回退到基线控制',
      tone: 'alert',
      textValue: true,
    }
  }
  const objective = String(aiTakeover?.last_objective || '').trim()
  if (aiTakeover?.state === 'ACTIVE' && objective) {
    return {
      key: 'strategy',
      label: '当前AI策略',
      value: objective,
      title: objective,
      tone: 'normal',
      textValue: true,
    }
  }
  return {
    key: 'strategy',
    label: '当前AI策略',
    value: '--',
    title: '当前没有已安装的AI策略目标',
    tone: 'muted',
    textValue: true,
  }
}

function nextDecisionCard(
  aiTakeover: AIControlStatus | null,
  enabled: boolean,
  elapsedSeconds: number | null | undefined,
  planning: boolean,
): RuntimeStatusCard {
  if (!enabled) {
    return {
      key: 'nextDecision',
      label: '下一次决策',
      value: '--',
      title: 'AI管控未开启',
      tone: 'muted',
    }
  }
  if (planning) {
    return {
      key: 'nextDecision',
      label: '下一次决策',
      value: '决策中',
      title: '正在重新规划管控策略',
      tone: 'planning',
      textValue: true,
    }
  }
  if (aiTakeover?.state !== 'ACTIVE') {
    return {
      key: 'nextDecision',
      label: '下一次决策',
      value: '--',
      title: '当前没有生效的AI计划',
      tone: 'muted',
    }
  }
  const validUntil = aiTakeover.plan_valid_until
  const elapsed = Number(elapsedSeconds)
  if (validUntil == null || !Number.isFinite(elapsed)) {
    return {
      key: 'nextDecision',
      label: '下一次决策',
      value: '即将决策',
      title: '计划有效期未知，等待下一轮决策',
      tone: 'planning',
      textValue: true,
    }
  }
  const remaining = Math.max(0, Math.ceil(validUntil - elapsed))
  if (remaining <= 0) {
    return {
      key: 'nextDecision',
      label: '下一次决策',
      value: '即将决策',
      title: '当前计划已到期，等待下一轮决策',
      tone: 'planning',
      textValue: true,
    }
  }
  return {
    key: 'nextDecision',
    label: '下一次决策',
    value: `${remaining} s`,
    title: `距离计划有效期还剩 ${remaining} 秒`,
    tone: 'normal',
  }
}
