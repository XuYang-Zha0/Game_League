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
  if (!['cs2', 'lol', 'valorant'].includes(gameId)) return null

  const response = await fetch(`/api/${gameId}/dataset`, {
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
  if (!['cs2', 'lol', 'valorant'].includes(gameId)) return null

  const params = new URLSearchParams()
  const view = String(options.view || 'fixture').trim()
  const date = String(options.date || '').trim()
  const tier = String(options.tier || 'b_or_above').trim()
  const limit = Number.parseInt(String(options.limit ?? ''), 10)
  const offset = Number.parseInt(String(options.offset ?? ''), 10)

  if (view) params.set('view', view)
  if (date) params.set('date', date)
  if (tier) params.set('tier', tier)
  if (Number.isFinite(limit) && limit > 0) params.set('limit', String(limit))
  if (Number.isFinite(offset) && offset >= 0) params.set('offset', String(offset))

  const query = params.toString()
  const response = await fetch(`/api/${gameId}/matches${query ? `?${query}` : ''}`, {
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
  if (!['cs2', 'lol', 'valorant'].includes(gameId) || !playerId) return null

  const response = await fetch(`/api/${gameId}/player/${encodeURIComponent(playerId)}`, {
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
  if (!['cs2', 'lol', 'valorant'].includes(gameId) || !teamKey) return null

  const response = await fetch(`/api/${gameId}/team/${encodeURIComponent(teamKey)}`, {
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

export const fetchAiChat = async (payload) => {
  const response = await fetch('/api/ai/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      gameId: payload.gameId || '',
      gameName: payload.gameName || '',
      page: payload.page || '',
      question: payload.question || '',
      context: payload.context || {},
      history: Array.isArray(payload.history) ? payload.history : [],
    }),
  })

  if (!response.ok) {
    const text = await response.text().catch(() => '')
    throw new Error(`AI request failed: ${response.status}${text ? ` — ${text}` : ''}`)
  }

  const data = await response.json()
  if (!data?.success || !data?.data) {
    throw new Error('AI returned invalid payload')
  }

  return data.data
}

export const fetchAiStatus = async () => {
  const response = await fetch('/api/ai/status', {
    method: 'GET',
    headers: { Accept: 'application/json' },
  })

  if (!response.ok) {
    throw new Error(`AI status request failed: ${response.status}`)
  }

  const payload = await response.json()
  return payload?.data || null
}

export const fetchAiWelcome = async (gameId) => {
  const response = await fetch(`/api/ai/welcome/${encodeURIComponent(gameId || 'cs2')}`, {
    method: 'GET',
    headers: { Accept: 'application/json' },
  })

  if (!response.ok) {
    throw new Error(`AI welcome request failed: ${response.status}`)
  }

  const payload = await response.json()
  return payload?.data?.message || ''
}

export const fetchAiChatStream = (payload, onChunk, onDone, onError) => {
  const controller = new AbortController()

  fetch('/api/ai/chat/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      gameId: payload.gameId || '',
      gameName: payload.gameName || '',
      page: payload.page || '',
      question: payload.question || '',
      context: payload.context || {},
      history: Array.isArray(payload.history) ? payload.history : [],
    }),
    signal: controller.signal,
  })
    .then(async (response) => {
      if (!response.ok) {
        const text = await response.text().catch(() => '')
        onError(new Error(`AI stream failed: ${response.status}${text ? ` — ${text}` : ''}`))
        return
      }
      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })

        const lines = buffer.split('\n')
        buffer = lines.pop() || ''

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          const dataStr = line.slice(6)
          try {
            const data = JSON.parse(dataStr)
            if (data.c) {
              onChunk(data.c)
            } else if (data.done) {
              onDone()
            } else if (data.error) {
              onError(new Error(data.error))
            }
          } catch (e) {
            // Ignore parse errors for partial chunks
          }
        }
      }
    })
    .catch((err) => {
      if (err.name !== 'AbortError') {
        onError(err)
      }
    })

  return controller
}

export const fetchBackendMatchDetail = async (gameId, matchId) => {
  if (!['cs2', 'lol', 'valorant'].includes(gameId) || !matchId) return null

  const response = await fetch(`/api/${gameId}/match/${encodeURIComponent(matchId)}`, {
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
