import { normalizeByGame } from './adapters'
import { gameCatalog, platformLayers, rawGameData } from './rawSources'

const normalizedCache = Object.fromEntries(
  gameCatalog.map((game) => [game.id, normalizeByGame(game.id, rawGameData[game.id])]),
)

export const getGameCatalog = () => gameCatalog

export const getPlatformLayers = () => platformLayers

export const getIntegratedDataset = () => normalizedCache

export const createExportPayload = (gameId, datasetOverride = null) => {
  const dataset = datasetOverride || normalizedCache[gameId]
  if (!dataset) return null

  return {
    exportedAt: new Date().toISOString(),
    gameId: dataset.gameId,
    gameName: dataset.gameName,
    updatedAt: dataset.updatedAt,
    metrics: dataset.metrics,
    leaderboard: dataset.leaderboard,
    tournaments: dataset.tournaments,
    matches: dataset.matches,
    teams: dataset.teams,
    players: dataset.players,
    analysis: dataset.analysis,
    analysisOutput: dataset.analysisOutput,
  }
}

export const fetchBackendDataset = async (gameId) => {
  if (gameId !== 'cs2') return null

  const response = await fetch('/api/cs2/dataset', {
    method: 'GET',
    headers: { Accept: 'application/json' },
  })

  if (!response.ok) {
    throw new Error(`Backend request failed: ${response.status}`)
  }

  const payload = await response.json()
  if (!payload?.success || !payload?.data) {
    throw new Error('Backend returned invalid dataset payload')
  }

  return payload.data
}

export const fetchBackendLiveMatches = async (gameId) => {
  if (gameId !== 'cs2') return null

  const response = await fetch('/api/cs2/live', {
    method: 'GET',
    headers: { Accept: 'application/json' },
  })

  if (!response.ok) {
    throw new Error(`Backend live request failed: ${response.status}`)
  }

  const payload = await response.json()
  if (!payload?.success || !payload?.data) {
    throw new Error('Backend returned invalid live payload')
  }

  return payload.data
}

export const fetchBackendScheduleMatches = async (gameId, options = {}) => {
  if (gameId !== 'cs2') return null

  const params = new URLSearchParams()
  const view = String(options.view || 'fixture').trim()
  const date = String(options.date || '').trim()
  const tier = String(options.tier || 'b_or_above').trim()
  const limit = Number.parseInt(String(options.limit ?? ''), 10)

  if (view) params.set('view', view)
  if (date) params.set('date', date)
  if (tier) params.set('tier', tier)
  if (Number.isFinite(limit) && limit > 0) params.set('limit', String(limit))

  const query = params.toString()
  const response = await fetch(`/api/cs2/matches${query ? `?${query}` : ''}`, {
    method: 'GET',
    headers: { Accept: 'application/json' },
  })

  if (!response.ok) {
    throw new Error(`Backend schedule request failed: ${response.status}`)
  }

  const payload = await response.json()
  if (!payload?.success || !payload?.data) {
    throw new Error('Backend returned invalid schedule payload')
  }

  return payload.data
}

export const fetchBackendPlayerDetail = async (gameId, playerId) => {
  if (gameId !== 'cs2' || !playerId) return null

  const response = await fetch(`/api/cs2/player/${encodeURIComponent(playerId)}`, {
    method: 'GET',
    headers: { Accept: 'application/json' },
  })

  if (!response.ok) {
    throw new Error(`Backend request failed: ${response.status}`)
  }

  const payload = await response.json()
  if (!payload?.success || !payload?.data) {
    throw new Error('Backend returned invalid player detail payload')
  }

  return payload.data
}

export const fetchBackendTeamDetail = async (gameId, teamKey) => {
  if (gameId !== 'cs2' || !teamKey) return null

  const response = await fetch(`/api/cs2/team/${encodeURIComponent(teamKey)}`, {
    method: 'GET',
    headers: { Accept: 'application/json' },
  })

  if (!response.ok) {
    throw new Error(`Backend request failed: ${response.status}`)
  }

  const payload = await response.json()
  if (!payload?.success || !payload?.data) {
    throw new Error('Backend returned invalid team detail payload')
  }

  return payload.data
}

export const fetchBackendMatchDetail = async (gameId, matchId) => {
  if (gameId !== 'cs2' || !matchId) return null

  const response = await fetch(`/api/cs2/match/${encodeURIComponent(matchId)}`, {
    method: 'GET',
    headers: { Accept: 'application/json' },
  })

  if (!response.ok) {
    throw new Error(`Backend match detail request failed: ${response.status}`)
  }

  const payload = await response.json()
  if (!payload?.success || !payload?.data) {
    throw new Error('Backend returned invalid match detail payload')
  }

  return payload.data
}
