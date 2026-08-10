// traffic_style消费：有后端等级时直接使用，不再本地阈值重算

import assert from 'node:assert/strict'
import test from 'node:test'

function normalizeCongestionLevel(value) {
  if (value === 'slow' || value === 'congested' || value === 'severe' || value === 'free') return value
  return 'free'
}

function worseCongestionLevel(left, right) {
  const rank = { free: 0, slow: 1, congested: 2, severe: 3 }
  return rank[left] >= rank[right] ? left : right
}

function buildIntersectionCongestionLevels(snapshot, trafficStyle) {
  const levels = {}
  if (!snapshot) return levels
  const edges = trafficStyle?.edges
  if (!edges || Object.keys(edges).length === 0) {
    for (const intersectionId of Object.keys(snapshot.intersections ?? {})) levels[intersectionId] = 'free'
    return levels
  }
  for (const [intersectionId, intersection] of Object.entries(snapshot.intersections ?? {})) {
    let level = 'free'
    for (const [laneId, lane] of Object.entries(intersection.lanes ?? {})) {
      const edgeId = String(lane.edge_id || laneId.replace(/_\d+$/, ''))
      const styled = edges[edgeId]
      if (!styled) continue
      level = worseCongestionLevel(level, normalizeCongestionLevel(styled.level))
    }
    levels[intersectionId] = level
  }
  return levels
}

test('uses backend traffic_style level and ignores local occupancy thresholds', () => {
  const snapshot = {
    intersections: {
      demo_1: {
        lanes: {
          E1_0: {
            edge_id: 'E1',
            vehicle_count: 20,
            halting_count: 20,
            mean_speed: 0.1,
            occupancy: 90,
          },
        },
      },
    },
  }
  const style = {
    as_of_seconds: 10,
    edges: {
      E1: {
        level: 'slow',
        score: 0.45,
        mean_speed: 0.1,
        occupancy_pct: 90,
        vehicle_count: 20,
        halting_count: 20,
      },
    },
  }
  const levels = buildIntersectionCongestionLevels(snapshot, style)
  assert.equal(levels.demo_1, 'slow')
})

test('missing traffic_style defaults to free without local recalculation', () => {
  const snapshot = {
    intersections: {
      demo_1: {
        lanes: {
          E1_0: {
            edge_id: 'E1',
            vehicle_count: 20,
            halting_count: 20,
            mean_speed: 0.1,
            occupancy: 90,
          },
        },
      },
    },
  }
  const levels = buildIntersectionCongestionLevels(snapshot, null)
  assert.equal(levels.demo_1, 'free')
})
