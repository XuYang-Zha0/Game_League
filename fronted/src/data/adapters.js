import { gameCatalog } from './rawSources'

const toPercent = (value) => `${Math.round(value * 100)}%`

const toTrend = (value) => {
  if (value > 0) return `+${value}`
  return `${value}`
}

const byId = Object.fromEntries(gameCatalog.map((item) => [item.id, item]))

const summarizeMetrics = (dataset) => {
  const tournamentCount = dataset.tournaments.length
  const matchCount = dataset.matches.length
  const teamCount = dataset.teams.length
  const playerCount = dataset.players.length
  const avgWinRate =
    dataset.leaderboard.reduce((acc, row) => acc + row.winRateRaw, 0) /
    Math.max(dataset.leaderboard.length, 1)

  return [
    {
      label: '收录赛事',
      value: String(tournamentCount),
      detail: '可切换查看 S/A 级赛事',
    },
    {
      label: '收录比赛',
      value: String(matchCount),
      detail: '支持多游戏统一检索',
    },
    {
      label: '战队规模',
      value: String(teamCount),
      detail: '同站点跨项目对比',
    },
    {
      label: '选手样本',
      value: String(playerCount),
      detail: `平均胜率 ${toPercent(avgWinRate)}`,
    },
  ]
}

const buildFilters = (dataset) => ({
  regions: [...new Set(dataset.tournaments.map((item) => item.region))],
  tiers: [...new Set(dataset.tournaments.map((item) => item.tier))],
})

const buildOutputRows = (dataset) => [
  { key: '当前项目', value: dataset.gameName },
  { key: '最新同步', value: dataset.updatedAt },
  { key: '赛事总量', value: `${dataset.tournaments.length} 场` },
  { key: '比赛总量', value: `${dataset.matches.length} 场` },
  { key: '战队总量', value: `${dataset.teams.length} 支` },
  { key: '选手总量', value: `${dataset.players.length} 人` },
]

const commonMappingNotes = [
  {
    title: '排行榜映射',
    desc: '不同项目统一映射为 rank/name/region/winRate/trend。',
  },
  {
    title: '赛事映射',
    desc: '将 event/league 信息统一映射到 name/tier/region/start/status。',
  },
  {
    title: '比赛映射',
    desc: '统一成 teamA/teamB/score/winner/stage，便于跨游戏对比。',
  },
]

const cs2Adapter = (raw) => {
  const gameMeta = byId.cs2
  const leaderboard = raw.ranking_board
    .map((row, index) => ({
      rank: index + 1,
      name: row.team_tag,
      region: row.area,
      points: row.elo_points,
      winRate: toPercent(row.wr_30d),
      winRateRaw: row.wr_30d,
      trend: toTrend(row.trend_delta),
    }))
    .sort((a, b) => a.rank - b.rank)

  const tournaments = raw.event_hub.map((item) => ({
    name: item.event_name,
    tier: item.tier,
    region: item.region,
    start: item.start_date,
    status: item.status,
    prize: `$${item.prize_usd.toLocaleString()}`,
  }))

  const matches = raw.recent_series.map((item) => ({
    date: item.match_date,
    tournament: item.event_name,
    stage: `${item.stage} · ${item.bo}`,
    teamA: item.left_team,
    teamB: item.right_team,
    score: item.score,
    winner: item.winner,
    note: item.note,
  }))

  const teams = raw.club_center.map((item) => ({
    name: item.full_name,
    region: item.area,
    coach: item.coach_name,
    style: item.style,
    form: item.form,
  }))

  const players = raw.player_pool.map((item) => ({
    name: item.nickname,
    team: item.team_tag,
    role: item.position,
    rating: item.rating_21.toFixed(2),
    impact: item.impact.toFixed(2),
    highlight: `ADR ${item.adr}`,
  }))

  const dataset = {
    gameId: gameMeta.id,
    gameName: gameMeta.name,
    gameSubtitle: gameMeta.subtitle,
    color: gameMeta.color,
    updatedAt: raw.updated_at,
    leaderboard,
    tournaments,
    matches,
    teams,
    players,
    analysis: {
      summary: raw.ai_brief.summary,
      turningPoints: raw.ai_brief.turning_points,
      teamInsight: raw.ai_brief.team_insight,
      playerInsight: raw.ai_brief.player_insight,
    },
    mappingNotes: [
      ...commonMappingNotes,
      {
        title: 'CS2 指标适配',
        desc: 'rating2.1 / ADR / impact 映射为通用 rating 与 highlight 字段。',
      },
    ],
  }

  return {
    ...dataset,
    metrics: summarizeMetrics(dataset),
    filters: buildFilters(dataset),
    analysisOutput: buildOutputRows(dataset),
  }
}

const valorantAdapter = (raw) => {
  const gameMeta = byId.valorant
  const leaderboard = raw.standings
    .map((row, index) => ({
      rank: index + 1,
      name: row.org,
      region: row.region,
      points: row.circuit_pts,
      winRate: toPercent(row.match_win_rate),
      winRateRaw: row.match_win_rate,
      trend: toTrend(row.rank_move),
    }))
    .sort((a, b) => a.rank - b.rank)

  const tournaments = raw.events.map((item) => ({
    name: item.league,
    tier: item.grade,
    region: item.zone,
    start: item.start_at,
    status: item.state,
    prize: item.prize,
  }))

  const matches = raw.fixtures.map((item) => ({
    date: item.played_on,
    tournament: item.league,
    stage: `${item.week} · ${item.series_type}`,
    teamA: item.home,
    teamB: item.away,
    score: item.result,
    winner: item.winner,
    note: item.map_score,
  }))

  const teams = raw.org_profile.map((item) => ({
    name: item.org_name,
    region: item.zone,
    coach: item.coach,
    style: item.style,
    form: item.streak,
  }))

  const players = raw.pro_stats.map((item) => ({
    name: item.ign,
    team: item.org,
    role: item.role,
    rating: item.kd.toFixed(2),
    impact: item.clutch.toFixed(2),
    highlight: `ACS ${item.acs}`,
  }))

  const dataset = {
    gameId: gameMeta.id,
    gameName: gameMeta.name,
    gameSubtitle: gameMeta.subtitle,
    color: gameMeta.color,
    updatedAt: raw.update_time,
    leaderboard,
    tournaments,
    matches,
    teams,
    players,
    analysis: {
      summary: raw.llm_digest.summary,
      turningPoints: raw.llm_digest.turning_points,
      teamInsight: raw.llm_digest.team_insight,
      playerInsight: raw.llm_digest.player_insight,
    },
    mappingNotes: [
      ...commonMappingNotes,
      {
        title: '无畏契约指标适配',
        desc: 'ACS / K-D / clutch 统一映射为 rating、impact、highlight。',
      },
    ],
  }

  return {
    ...dataset,
    metrics: summarizeMetrics(dataset),
    filters: buildFilters(dataset),
    analysisOutput: buildOutputRows(dataset),
  }
}

const lolAdapter = (raw) => {
  const gameMeta = byId.lol
  const leaderboard = raw.power_rank
    .map((row, index) => ({
      rank: index + 1,
      name: row.club,
      region: row.region,
      points: row.power_score,
      winRate: toPercent(row.game_wr),
      winRateRaw: row.game_wr,
      trend: toTrend(row.rank_trend),
    }))
    .sort((a, b) => a.rank - b.rank)

  const tournaments = raw.leagues.map((item) => ({
    name: item.league_name,
    tier: item.tier_level,
    region: item.area,
    start: item.kickoff,
    status: item.progress,
    prize: item.bonus_pool,
  }))

  const matches = raw.games.map((item) => ({
    date: item.match_day,
    tournament: item.league,
    stage: `${item.stage} · ${item.bo}`,
    teamA: item.blue,
    teamB: item.red,
    score: item.score,
    winner: item.winner,
    note: `Patch ${item.patch}`,
  }))

  const teams = raw.clubs.map((item) => ({
    name: item.club_name,
    region: item.area,
    coach: item.coach,
    style: item.style,
    form: item.status,
  }))

  const players = raw.summoners.map((item) => ({
    name: item.player,
    team: item.club,
    role: item.lane,
    rating: item.kda.toFixed(2),
    impact: item.kp.toFixed(2),
    highlight: `DPM ${item.dpm}`,
  }))

  const dataset = {
    gameId: gameMeta.id,
    gameName: gameMeta.name,
    gameSubtitle: gameMeta.subtitle,
    color: gameMeta.color,
    updatedAt: raw.last_sync,
    leaderboard,
    tournaments,
    matches,
    teams,
    players,
    analysis: {
      summary: raw.smart_report.summary,
      turningPoints: raw.smart_report.turning_points,
      teamInsight: raw.smart_report.team_insight,
      playerInsight: raw.smart_report.player_insight,
    },
    mappingNotes: [
      ...commonMappingNotes,
      {
        title: '英雄联盟指标适配',
        desc: 'KDA / DPM / 参团率统一为 rating、highlight、impact。',
      },
    ],
  }

  return {
    ...dataset,
    metrics: summarizeMetrics(dataset),
    filters: buildFilters(dataset),
    analysisOutput: buildOutputRows(dataset),
  }
}

export const normalizeByGame = (gameId, raw) => {
  if (gameId === 'cs2') return cs2Adapter(raw)
  if (gameId === 'valorant') return valorantAdapter(raw)
  if (gameId === 'lol') return lolAdapter(raw)
  throw new Error(`Unknown game id: ${gameId}`)
}
