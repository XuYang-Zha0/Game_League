<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts/core'
import { BarChart, GaugeChart, LineChart, PieChart, RadarChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import {
  createExportPayload,
  fetchBackendDataset,
  fetchBackendLiveMatches,
  fetchBackendMatchDetail,
  fetchBackendPlayerDetail,
  fetchBackendScheduleMatches,
  fetchBackendTeamDetail,
  getGameCatalog,
  getIntegratedDataset,
} from './data/platformService'
import SchedulePage from './components/SchedulePage.vue'
import MatchDetailPage from './components/MatchDetailPage.vue'
import TournamentDetailPage from './components/TournamentDetailPage.vue'
import AiChatWidget from './components/AiChatWidget.vue'

echarts.use([BarChart, GaugeChart, LineChart, PieChart, RadarChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer])

const gameCatalog = getGameCatalog()
const integratedDataset = getIntegratedDataset()
const emptyBackendDataset = (gameId, gameName, gameSubtitle, color) => ({
  gameId,
  gameName,
  gameSubtitle,
  color,
  updatedAt: '',
  matches: [],
  tournaments: [],
  leaderboard: [],
  teams: [],
  players: [],
  totals: {},
  metrics: [],
  analysisOutput: [],
  filters: { regions: [], tiers: [] },
  analysis: {
    summary: '',
    turningPoints: [],
    teamInsight: '',
    playerInsight: '',
  },
})
const staticDatasetFallback = (gameId, gameName, gameSubtitle, color) => {
  const base = integratedDataset?.[gameId]
  if (base && typeof base === 'object') {
    return {
      gameId,
      gameName: base.gameName || gameName,
      gameSubtitle: base.gameSubtitle || gameSubtitle,
      color: base.color || color,
      updatedAt: base.updatedAt || '',
      matches: Array.isArray(base.matches) ? base.matches : [],
      tournaments: Array.isArray(base.tournaments) ? base.tournaments : [],
      leaderboard: Array.isArray(base.leaderboard) ? base.leaderboard : [],
      teams: Array.isArray(base.teams) ? base.teams : [],
      players: Array.isArray(base.players) ? base.players : [],
      totals: base.totals || {},
      metrics: Array.isArray(base.metrics) ? base.metrics : [],
      analysisOutput: Array.isArray(base.analysisOutput) ? base.analysisOutput : [],
      filters: base.filters || { regions: [], tiers: [] },
      analysis: base.analysis || {
        summary: '',
        turningPoints: [],
        teamInsight: '',
        playerInsight: '',
      },
    }
  }
  return emptyBackendDataset(gameId, gameName, gameSubtitle, color)
}
const initialDataset = {
  ...integratedDataset,
  cs2: staticDatasetFallback('cs2', 'CS2', 'Counter-Strike 2', '#1f6feb'),
  valorant: staticDatasetFallback('valorant', '无畏契约', 'VALORANT', '#e63946'),
  lol: staticDatasetFallback('lol', '英雄联盟', 'League of Legends', '#0f8b8d'),
}
const fallbackDataset = {
  color: '#1f6feb',
  matches: [],
  tournaments: [],
  leaderboard: [],
  teams: [],
  players: [],
  filters: { regions: [], tiers: [] },
}
const validGameIds = new Set(gameCatalog.map((item) => item.id))
const pageKeys = ['home', 'schedule', 'match-detail', 'tournaments', 'tournament-detail', 'teams', 'players', 'player-detail', 'team-detail']

const gameVisualMap = {
  cs2: {
    cover: '/images/covers/games/CS2_Background.jpg',
    logo: '/images/logos/games/cs2_logo.jpg',
    title: 'CS2 赛事数据中心',
  },
  valorant: {
    cover: '/images/covers/games/valorant_header.jpg',
    logo: '/images/logos/games/valorant_logo.jpg',
    title: '无畏契约赛事数据中心',
  },
  lol: {
    cover: '/images/covers/games/lol_header.jpg',
    logo: '/images/logos/games/lol_logo.jpg',
    title: '英雄联盟赛事数据中心',
  },
}

const datasetByGame = ref(initialDataset)
const selectedGameId = ref(gameCatalog[0]?.id || 'cs2')
const currentPage = ref('home')
const selectedPlayerId = ref('')
const selectedTeamKey = ref('')
const selectedMatchId = ref('')
const selectedTournamentKey = ref('')
const isGameMenuOpen = ref(false)
const switchBlockRef = ref(null)
const aiChatWidgetRef = ref(null)
const lastAutoAnalyzedMatchId = ref('')
const teamRadarChartRef = ref(null)
const teamRecordChartRef = ref(null)
const teamCoreChartRef = ref(null)
const teamTacticalChartRef = ref(null)
const playerAbilityChartRef = ref(null)
const playerMapChartRef = ref(null)
const playerRatingTrendChartRef = ref(null)
let teamRadarChart = null
let teamRecordChart = null
let teamCoreChart = null
let teamTacticalChart = null
let playerAbilityChart = null
let playerMapChart = null
let playerRatingTrendChart = null

const searchKeyword = ref('')

const playerDetailState = ref('idle')
const playerDetailError = ref('')
const playerDetail = ref(null)
const teamDetailState = ref('idle')
const teamDetailError = ref('')
const teamDetail = ref(null)
const matchDetailState = ref('idle')
const matchDetailError = ref('')
const matchDetail = ref(null)
const teamDetailTab = ref('data')
const teamRankMode = ref('valve')
const lolRegionFilter = ref('all')
const scheduleViewMode = ref('fixture')
const scheduleDateFilter = ref('')
const scheduleTierFilter = ref('b_or_above')
const scheduleTierFilterOptions = [
  { value: 'b_or_above', label: 'B及以上' },
  { value: 'a_or_above', label: 'A及以上' },
  { value: 's_or_above', label: 'S及以上' },
  { value: 'all', label: '全部级别' },
]
const scheduleTierThreshold = {
  b_or_above: 2,
  a_or_above: 3,
  s_or_above: 4,
}
const scheduleDateMin = '2023-01-01'
const SCHEDULE_LIVE_POLL_INTERVAL_MS = 7000
const BACKEND_DATASET_REFRESH_INTERVAL_MS = 30000
const SCHEDULE_PAGE_SIZE = 20
const TOURNAMENT_PAGE_SIZE = 20
const PLAYER_PAGE_SIZE = 120
const tournamentVisibleCount = ref(TOURNAMENT_PAGE_SIZE)
const playerVisibleCount = ref(PLAYER_PAGE_SIZE)
const homeTeamCarouselPage = ref(0)
const homePlayerCarouselPage = ref(0)
const expandedHomePanel = ref('')
let scheduleLivePollTimer = null
let scheduleLivePollInFlight = false
let backendDatasetRefreshTimer = null
let homeTeamCarouselTimer = null
let homePlayerCarouselTimer = null
let scheduleRowsRequestSeq = 0
const backendDatasetRequesting = new Set()

const activeDataset = computed(
  () => datasetByGame.value[selectedGameId.value] || datasetByGame.value[gameCatalog[0]?.id] || fallbackDataset,
)
const activeVisual = computed(() => gameVisualMap[selectedGameId.value] || gameVisualMap.cs2)
const activeGameName = computed(() => gameCatalog.find((item) => item.id === selectedGameId.value)?.name || 'CS2')
const isLolGame = computed(() => selectedGameId.value === 'lol')
const isValorantGame = computed(() => selectedGameId.value === 'valorant')
const isRegionRankGame = computed(() => isLolGame.value || isValorantGame.value)

const formatRoundEventForAi = (event, index) => {
  const type = String(event?.eventType || '').trim()
  const player = String(event?.playerName || '').trim()
  const related = String(event?.relatedPlayerName || '').trim()
  const weapon = String(event?.weapon || '').trim()
  const site = String(event?.bombSite || '').trim()
  const assister = String(event?.assisterName || '').trim()
  const base = {
    seq: index + 1,
    type,
    side: event?.teamSide || '',
    text: String(event?.eventText || '').trim(),
  }
  if (type === 'kill') {
    return {
      ...base,
      killer: player,
      victim: related,
      assister,
      weapon,
    }
  }
  if (type === 'bomb_planted') {
    return {
      ...base,
      planter: player,
      site,
    }
  }
  if (type === 'player_join' || type === 'player_quit') {
    return {
      ...base,
      player,
    }
  }
  return base.text ? base : null
}

const buildKeyKillCandidatesForAi = (events) => {
  const kills = events.filter((event) => event?.type === 'kill')
  const bombSeq = events.find((event) => event?.type === 'bomb_planted')?.seq
  const killCountByPlayer = new Map()
  return kills.map((event, index) => {
    const count = (killCountByPlayer.get(event.killer) || 0) + 1
    killCountByPlayer.set(event.killer, count)
    const reasons = []
    if (index === 0) reasons.push('首杀')
    if (bombSeq && event.seq > bombSeq) reasons.push('下包后击杀')
    if (count >= 2) reasons.push(`${event.killer}本回合第${count}次击杀`)
    if (index >= Math.max(0, kills.length - 2)) reasons.push('回合末段击杀')
    return {
      seq: event.seq,
      killer: event.killer,
      victim: event.victim,
      assister: event.assister,
      weapon: event.weapon,
      reason: reasons.join('、') || '中段击杀',
    }
  })
}

const buildRoundEventDigestForAi = (roundEvents) => {
  if (!Array.isArray(roundEvents)) return []
  return roundEvents.map((mapBlock) => ({
    mapIndex: mapBlock?.mapIndex,
    map: mapBlock?.mapName,
    rounds: (Array.isArray(mapBlock?.rounds) ? mapBlock.rounds : []).map((round) => {
      const events = (Array.isArray(round?.events) ? round.events : [])
        .filter((event) => !['round_start', 'round_end', 'match_started'].includes(event?.eventType))
        .map(formatRoundEventForAi)
        .filter(Boolean)
      return {
        round: round?.roundNumber,
        globalRound: round?.roundGlobalIndex,
        winner: round?.winnerSide || '',
        winType: round?.winType || '',
        score: round?.scoreCt != null && round?.scoreT != null ? `${round.scoreCt}:${round.scoreT}` : '',
        events,
        keyKillCandidates: buildKeyKillCandidatesForAi(events),
      }
    }),
  }))
}

const aiContextData = computed(() => {
  const dataset = activeDataset.value || fallbackDataset
  const ctx = {
    leaderboard: (dataset.leaderboard || []).slice(0, 10),
    tournaments: (dataset.tournaments || []).slice(0, 5),
    matches: (dataset.matches || []).slice(0, 10),
    teams: (dataset.teams || []).slice(0, 10),
    players: (dataset.players || []).slice(0, 10),
    analysis: dataset.analysis || {},
  }
  if (currentPage.value === 'match-detail' && matchDetail.value) {
    const { roundEvents: _, roundEventSummary: __, mapPlayerStats: ___, detailMetrics: ____, ...matchWithoutRoundEvents } = matchDetail.value
    ctx.currentMatch = matchWithoutRoundEvents

    const reSummary = matchDetail.value?.roundEventSummary
    const reMaps = matchDetail.value?.roundEvents
    if (reSummary && reSummary.eventCount > 0) {
      ctx.roundEventSummary = {
        totalEvents: reSummary.eventCount,
        totalRounds: reSummary.roundCount,
        mapCount: reSummary.mapCount,
        maps: (reSummary.maps || []).map((m) => ({
          map: m.mapName,
          rounds: m.roundCount,
          events: m.eventCount,
        })),
      }
      ctx.roundEventDigest = buildRoundEventDigestForAi(reMaps)
    }
  }
  if (currentPage.value === 'player-detail' && playerDetail.value) {
    ctx.currentPlayer = playerDetail.value
  }
  if (currentPage.value === 'team-detail' && teamDetail.value) {
    ctx.currentTeam = teamDetail.value
  }
  if (currentPage.value === 'tournament-detail') {
    ctx.currentTournament = selectedTournament.value
    ctx.currentTournamentTeams = tournamentTeamRows.value.slice(0, 20)
    ctx.currentTournamentMatches = tournamentMatchRows.value.slice(0, 30)
  }
  return ctx
})

const navItems = [
  { page: 'home', label: '首页' },
  { page: 'schedule', label: '比赛赛程' },
  { page: 'tournaments', label: '赛事' },
  { page: 'teams', label: '战队' },
  { page: 'players', label: '选手' },
]

const topHonors = computed(() => (playerDetail.value?.honors || []).slice(0, 40))
const lolAdvancedStats = computed(() => playerDetail.value?.advancedStats || [])
const lolChampionStats = computed(() => playerDetail.value?.championStats || [])
const lolCareerTeams = computed(() => playerDetail.value?.careerTeams || [])
const lolCareerProfile = computed(() => playerDetail.value?.careerProfile || {})
const lolRecentForm = computed(() => playerDetail.value?.recentForm || {})
const teamMembers = computed(() => {
  const rows = (teamDetail.value?.members || []).slice(0, 5).map((row) => ({
    ...row,
    position: row.position || row.role || '-',
  }))
  while (rows.length < 5) {
    rows.push({
      playerId: `placeholder-${rows.length + 1}`,
      name: '-',
      avatar: '',
      countryLogo: '',
      position: '-',
      rating: '-',
      isPlaceholder: true,
    })
  }
  return rows
})

const teamTextValue = (a, b = '-') => {
  const v = a ?? b
  const text = String(v ?? '').trim()
  return text || '-'
}

const teamRankingBrief = computed(() => {
  const rank = teamDetail.value?.rank || {}
  const stats = teamDetail.value?.stats || {}
  if (isLolGame.value) {
    return [
      { label: '世界排名', value: teamTextValue(rank.globalRank, stats.globalRank) },
      { label: '赛区排名', value: teamTextValue(rank.regionRank, stats.regionRank) },
      { label: '积分', value: teamTextValue(rank.score, stats.score || stats.rankScore) },
      { label: '排名变动', value: teamTextValue(rank.rankChange, stats.rankChange) },
    ]
  }
  if (isValorantGame.value) {
    return [
      { label: '赛区排名', value: teamTextValue(rank.regionRank, stats.regionRank) },
      { label: '全球排名', value: teamTextValue(rank.globalRank, stats.globalRank) },
      { label: '评分', value: teamTextValue(stats.rankScore, rank.score) },
      { label: 'Tier', value: teamTextValue(stats.tier) },
    ]
  }
  return [
    { label: '世界排名', value: teamTextValue(rank.globalRank, stats.globalRank) },
    { label: 'Valve 排名', value: teamTextValue(rank.valveRank, stats.valveRank) },
    { label: '总积分', value: teamTextValue(rank.score, stats.score) },
    { label: '排名变化', value: teamTextValue(rank.rankChange, stats.rankChange) },
  ]
})

const teamCoreStats = computed(() => {
  const stats = teamDetail.value?.stats || {}
  if (isLolGame.value) {
    return [
      { label: '胜率', value: teamTextValue(stats.winRate), ratio: teamMetricRatio(stats.winRate, 50) },
      { label: '胜场', value: teamTextValue(stats.wins), ratio: teamMetricRatio(stats.wins, 56) },
      { label: '比赛数', value: teamTextValue(stats.matchesPlayed), ratio: teamMetricRatio(stats.matchesPlayed, 62) },
      { label: '评分', value: teamTextValue(stats.rankScore), ratio: teamMetricRatio(stats.rankScore, 58) },
    ]
  }
  if (isValorantGame.value) {
    return [
      { label: '胜率', value: teamTextValue(stats.winRate), ratio: teamMetricRatio(stats.winRate, 50) },
      { label: '胜场', value: teamTextValue(stats.wins), ratio: teamMetricRatio(stats.wins, 56) },
      { label: '比赛数', value: teamTextValue(stats.matchesPlayed), ratio: teamMetricRatio(stats.matchesPlayed, 62) },
      { label: '评分', value: teamTextValue(stats.rankScore), ratio: teamMetricRatio(stats.rankScore, 58) },
      { label: '状态', value: teamTextValue(stats.status), ratio: stats.status && stats.status !== '-' ? 72 : 48 },
    ]
  }
  return [
    { label: 'Rating', value: teamTextValue(stats.rating), ratio: teamMetricRatio(stats.rating, 60) },
    { label: 'K/D', value: teamTextValue(stats.kd), ratio: teamMetricRatio(stats.kd, 58) },
    { label: '地图胜率', value: teamTextValue(stats.mapWinRate), ratio: teamMetricRatio(stats.mapWinRate, 50) },
    { label: '整体胜率', value: teamTextValue(stats.winRate), ratio: teamMetricRatio(stats.winRate, 50) },
    { label: '场均击杀', value: teamTextValue(stats.avgKill), ratio: teamMetricRatio(stats.avgKill, 62) },
    { label: '场均助攻', value: teamTextValue(stats.avgAssist), ratio: teamMetricRatio(stats.avgAssist, 54) },
  ]
})

const teamCoreSummaryStats = computed(() => teamCoreStats.value.slice(0, 4))

const teamTacticalStats = computed(() => {
  const stats = teamDetail.value?.stats || {}
  if (isLolGame.value) return []
  if (isValorantGame.value) return []
  return [
    { label: '手枪局', value: teamTextValue(stats.firstFiveWinRate), ratio: teamMetricRatio(stats.firstFiveWinRate, 50) },
    { label: '前十回合', value: teamTextValue(stats.firstTenWinRate), ratio: teamMetricRatio(stats.firstTenWinRate, 50) },
    { label: 'CT 首杀', value: teamTextValue(stats.ctFirstWinRate), ratio: teamMetricRatio(stats.ctFirstWinRate, 50) },
    { label: 'T 首杀', value: teamTextValue(stats.tFirstWinRate), ratio: teamMetricRatio(stats.tFirstWinRate, 50) },
    { label: 'CT 胜率', value: teamTextValue(stats.ctWinRate), ratio: teamMetricRatio(stats.ctWinRate, 50) },
    { label: 'T 胜率', value: teamTextValue(stats.tWinRate), ratio: teamMetricRatio(stats.tWinRate, 50) },
  ]
})

const teamTacticalSummaryStats = computed(() => [
  { label: '首杀率', value: teamTextValue(teamDetail.value?.stats?.firstKillRate) },
  { label: '首死率', value: teamTextValue(teamDetail.value?.stats?.firstDeathRate) },
  { label: '总回合', value: teamTextValue(teamDetail.value?.stats?.totalRound) },
  { label: '地图胜负', value: teamTextValue(teamDetail.value?.stats?.mapWinLoss) },
].filter((item) => item.value && item.value !== '-'))

const teamHeroKpis = computed(() => {
  const rank = teamDetail.value?.rank || {}
  const stats = teamDetail.value?.stats || {}
  if (isLolGame.value) {
    return [
      { label: '世界排名', value: teamTextValue(rank.globalRank, stats.globalRank), tone: 'rank' },
      { label: '比赛数', value: teamTextValue(stats.matchesPlayed), tone: 'sample' },
      { label: '胜场', value: teamTextValue(stats.wins), tone: 'win' },
      { label: '胜率', value: teamTextValue(stats.winRate), tone: 'rate' },
    ]
  }
  if (isValorantGame.value) {
    return [
      { label: '赛区排名', value: teamTextValue(rank.regionRank, stats.regionRank), tone: 'rank' },
      { label: '全球参考', value: teamTextValue(rank.globalRank, stats.globalRank), tone: 'sample' },
      { label: '评分', value: teamTextValue(stats.rankScore, rank.score), tone: 'rate' },
      { label: '状态', value: teamTextValue(stats.status, stats.tier), tone: 'win' },
    ]
  }
  return [
    { label: '世界排名', value: teamTextValue(rank.globalRank, stats.globalRank), tone: 'rank' },
    { label: 'Valve 排名', value: teamTextValue(rank.valveRank, stats.valveRank), tone: 'sample' },
    { label: 'Rating', value: teamTextValue(stats.rating), tone: 'rate' },
    { label: '手枪局胜率', value: teamTextValue(stats.firstFiveWinRate), tone: 'win' },
  ]
})

const teamMetricRatio = (value, fallback = 58) => {
  const text = String(value ?? '').trim()
  if (!text || text === '-') return fallback
  const pct = text.match(/-?\d+(\.\d+)?\s*%/)
  if (pct) return clamp(Number.parseFloat(pct[0]), 0, 100)
  const num = Number.parseFloat(text.replace(/[^0-9.+-]/g, ''))
  if (!Number.isFinite(num)) return fallback
  if (num <= 2) return clamp(num * 50, 0, 100)
  if (num <= 10) return clamp(num * 10, 0, 100)
  return clamp(num, 0, 100)
}

const teamIdentityChips = computed(() => {
  const basic = teamDetail.value?.basic || {}
  const stats = teamDetail.value?.stats || {}
  const rank = teamDetail.value?.rank || {}
  return [
    basic.region ? `赛区 ${basic.region}` : '',
    rank.globalRank || stats.globalRank ? `世界 #${rank.globalRank || stats.globalRank}` : '',
    rank.valveRank || stats.valveRank ? `Valve #${rank.valveRank || stats.valveRank}` : '',
    stats.tier ? `Tier ${stats.tier}` : '',
    stats.status ? `状态 ${stats.status}` : '',
  ].filter(Boolean)
})

const isTeamRecentResultRow = (row) => {
  const result = String(row?.result || row?.winner || '').trim().toLowerCase()
  const score = String(row?.score || '').trim()
  const statusCode = Number(row?.statusCode ?? row?.status_code)
  const hasResult = ['胜', '负', '平', 'win', 'loss', 'draw', 'w', 'l'].some((key) => result.includes(key))
  const hasScore = Boolean(score && score !== '-' && score !== '-:-' && score !== '0:0' && score !== '0-0')
  if (Number.isFinite(statusCode) && statusCode !== 2) return false
  return hasResult && hasScore
}

const teamRecentMatches = computed(() => (teamDetail.value?.recentMatches || []).filter(isTeamRecentResultRow).slice(0, 8))

const teamResultClass = (value) => {
  const text = String(value || '').trim().toLowerCase()
  if (['w', 'win', '胜', '获胜'].some((key) => text.includes(key))) return 'win'
  if (['l', 'loss', '负', '失败'].some((key) => text.includes(key))) return 'loss'
  return 'unknown'
}

const teamChartMetrics = computed(() => {
  const stats = teamDetail.value?.stats || {}
  if (isLolGame.value) {
    return [
      { name: '胜率', value: teamMetricRatio(stats.winRate, 50) },
      { name: '胜场', value: teamMetricRatio(stats.wins, 56) },
      { name: '比赛量', value: teamMetricRatio(stats.matchesPlayed, 62) },
      { name: '评分', value: teamMetricRatio(stats.rankScore, 58) },
    ]
  }
  if (isValorantGame.value) {
    return [
      { name: '胜率', value: teamMetricRatio(stats.winRate, 50) },
      { name: '评分', value: teamMetricRatio(stats.rankScore || stats.rating, 62) },
      { name: '胜场', value: teamMetricRatio(stats.wins, 56) },
      { name: '样本', value: teamMetricRatio(stats.matchesPlayed, 64) },
      { name: '状态', value: stats.status && stats.status !== '-' ? 72 : 48 },
    ]
  }
  return [
    { name: 'Rating', value: teamMetricRatio(stats.rating, 60) },
    { name: 'K/D', value: teamMetricRatio(stats.kd, 58) },
    { name: '地图胜率', value: teamMetricRatio(stats.mapWinRate, 50) },
    { name: '手枪局胜率', value: teamMetricRatio(stats.firstFiveWinRate, 50) },
    { name: '首杀率', value: teamMetricRatio(stats.firstKillRate, 52) },
    { name: '场均击杀', value: teamMetricRatio(stats.avgKill, 62) },
  ]
})

const teamRecordSummary = computed(() => {
  const rows = teamRecentMatches.value
  let wins = 0
  let losses = 0
  for (const row of rows) {
    const cls = teamResultClass(row?.result)
    if (cls === 'win') wins += 1
    else if (cls === 'loss') losses += 1
  }
  return { wins, losses, total: rows.length }
})

const teamInsightLines = computed(() => {
  const stats = teamDetail.value?.stats || {}
  const rank = teamDetail.value?.rank || {}
  const basic = teamDetail.value?.basic || {}
  return [
    `${basic.teamName || '当前战队'} 当前${basic.region ? `位于 ${basic.region} 赛区` : '已载入详情'}，核心排名参考为 ${teamTextValue(rank.globalRank || rank.regionRank || stats.globalRank || stats.regionRank)}。`,
    `近期样本包含 ${teamRecentMatches.value.length} 场比赛，战绩记录为 ${teamRecordSummary.value.wins} 胜 / ${teamRecordSummary.value.losses} 负。`,
    `核心指标：Rating ${teamTextValue(stats.rating)}，K/D ${teamTextValue(stats.kd)}，整体胜率 ${teamTextValue(stats.winRate)}，手枪局胜率 ${teamTextValue(stats.firstFiveWinRate)}。`,
  ]
})

const triggerTeamAiAnalysis = async () => {
  if (!teamDetail.value?.basic) return
  const basic = teamDetail.value.basic
  const stats = teamDetail.value.stats || {}
  const rank = teamDetail.value.rank || {}
  const members = (teamDetail.value.members || []).map((row) => row.name || row.playerName).filter(Boolean).slice(0, 6).join('、')
  const recent = teamRecentMatches.value
    .slice(0, 5)
    .map((row) => `${row.date || '-'} ${row.teamName || basic.teamName || '-'} vs ${row.opponent || '-'} ${row.score || '-'} ${row.result || ''}`)
    .join('；')
  const question = `请分析${activeGameName.value}战队 ${basic.teamName || '-'}。赛区：${basic.region || '-'}；排名：世界${rank.globalRank || stats.globalRank || '-'}，赛区${rank.regionRank || stats.regionRank || '-'}，Valve${rank.valveRank || stats.valveRank || '-'}；关键指标：胜率${stats.winRate || '-'}，评分${stats.rating || stats.rankScore || rank.score || '-'}，K/D${stats.kd || '-'}，手枪局胜率${stats.firstFiveWinRate || '-'}，前十回合胜率${stats.firstTenWinRate || '-'}，CT首杀胜率${stats.ctFirstWinRate || '-'}，T首杀胜率${stats.tFirstWinRate || '-'}；成员：${members || '-'}；近期比赛：${recent || '暂无'}。请从近期状态、优势短板、关键成员和后续关注点四方面总结。`
  await nextTick()
  aiChatWidgetRef.value?.autoAnalyze(question, aiContextData.value)
}

const teamRadarOption = computed(() => ({
  backgroundColor: 'transparent',
  tooltip: { trigger: 'item' },
  radar: {
    radius: '64%',
    indicator: teamChartMetrics.value.map((item) => ({ name: item.name, max: 100 })),
    axisName: { color: '#dbe8f7', fontSize: 11 },
    splitLine: { lineStyle: { color: 'rgba(180, 205, 238, 0.22)' } },
    splitArea: { areaStyle: { color: ['rgba(255,255,255,0.03)', 'rgba(255,255,255,0.015)'] } },
    axisLine: { lineStyle: { color: 'rgba(180, 205, 238, 0.2)' } },
  },
  series: [
    {
      type: 'radar',
      data: [
        {
          value: teamChartMetrics.value.map((item) => item.value),
          name: '综合能力',
          areaStyle: { color: 'rgba(80, 143, 255, 0.24)' },
          lineStyle: { color: '#6ea8ff', width: 2 },
          itemStyle: { color: '#ffffff', borderColor: '#6ea8ff' },
        },
      ],
    },
  ],
}))

const teamRecordOption = computed(() => ({
  backgroundColor: 'transparent',
  tooltip: { trigger: 'axis' },
  grid: { left: 12, right: 12, top: 18, bottom: 18, containLabel: true },
  xAxis: {
    type: 'category',
    data: ['胜场', '负场'],
    axisLabel: { color: '#dbe8f7' },
    axisLine: { lineStyle: { color: 'rgba(180, 205, 238, 0.25)' } },
    axisTick: { show: false },
  },
  yAxis: {
    type: 'value',
    minInterval: 1,
    axisLabel: { color: '#9fb2ca' },
    splitLine: { lineStyle: { color: 'rgba(180, 205, 238, 0.12)' } },
  },
  series: [
    {
      type: 'bar',
      barWidth: 28,
      data: [
        { value: teamRecordSummary.value.wins, itemStyle: { color: '#39d98a' } },
        { value: teamRecordSummary.value.losses, itemStyle: { color: '#ff6b7a' } },
      ],
    },
  ],
}))

const teamCoreOption = computed(() => ({
  backgroundColor: 'transparent',
  tooltip: {
    trigger: 'axis',
    formatter: (items) => {
      const item = items?.[0]
      const raw = teamCoreStats.value[item?.dataIndex]
      return raw ? `${raw.label}<br/>原始值：${raw.value}<br/>标准化：${Math.round(raw.ratio)}%` : ''
    },
  },
  grid: { left: 12, right: 12, top: 18, bottom: 22, containLabel: true },
  xAxis: {
    type: 'category',
    data: teamCoreStats.value.map((item) => item.label),
    axisLabel: { color: '#cfe0f5', fontSize: 11, interval: 0 },
    axisLine: { lineStyle: { color: 'rgba(180, 205, 238, 0.22)' } },
    axisTick: { show: false },
  },
  yAxis: {
    type: 'value',
    max: 100,
    axisLabel: { color: '#8fa5c1', formatter: '{value}%' },
    splitLine: { lineStyle: { color: 'rgba(180, 205, 238, 0.1)' } },
  },
  series: [
    {
      type: 'bar',
      barWidth: 24,
      data: teamCoreStats.value.map((item, index) => ({
        value: item.ratio,
        itemStyle: {
          color: index < 2 ? '#6ea8ff' : index < 4 ? '#39d98a' : '#f6c85f',
          borderRadius: [8, 8, 2, 2],
        },
      })),
    },
  ],
}))

const teamTacticalOption = computed(() => ({
  backgroundColor: 'transparent',
  tooltip: {
    trigger: 'item',
    formatter: (params) => {
      const raw = teamTacticalStats.value[params.dataIndex]
      return raw ? `${raw.label}<br/>${raw.value}` : ''
    },
  },
  series: [
    {
      type: 'pie',
      radius: ['42%', '72%'],
      center: ['50%', '50%'],
      roseType: 'radius',
      label: {
        color: '#dbe8f7',
        formatter: (params) => {
          const raw = teamTacticalStats.value[params.dataIndex]
          return raw ? `${raw.label}\n${raw.value}` : ''
        },
      },
      labelLine: { lineStyle: { color: 'rgba(219, 232, 247, 0.35)' } },
      data: teamTacticalStats.value.map((item, index) => ({
        name: item.label,
        value: Math.max(12, item.ratio),
        itemStyle: {
          color: ['#6ea8ff', '#8f7cff', '#39d98a', '#f6c85f', '#ff8f70', '#51d0de'][index % 6],
          borderColor: 'rgba(9, 18, 34, 0.75)',
          borderWidth: 2,
        },
      })),
    },
  ],
}))

const renderTeamCharts = async () => {
  await nextTick()
  if (currentPage.value !== 'team-detail' || !teamDetail.value?.basic) return
  if (teamRadarChartRef.value) {
    teamRadarChart ||= echarts.init(teamRadarChartRef.value)
    teamRadarChart.setOption(teamRadarOption.value, true)
  }
  if (teamRecordChartRef.value) {
    teamRecordChart ||= echarts.init(teamRecordChartRef.value)
    teamRecordChart.setOption(teamRecordOption.value, true)
  }
  if (teamCoreChartRef.value) {
    teamCoreChart ||= echarts.init(teamCoreChartRef.value)
    teamCoreChart.setOption(teamCoreOption.value, true)
  }
  if (teamTacticalChartRef.value) {
    teamTacticalChart ||= echarts.init(teamTacticalChartRef.value)
    teamTacticalChart.setOption(teamTacticalOption.value, true)
  }
}

const resizeTeamCharts = () => {
  teamRadarChart?.resize()
  teamRecordChart?.resize()
  teamCoreChart?.resize()
  teamTacticalChart?.resize()
}

const disposeTeamCharts = () => {
  teamRadarChart?.dispose()
  teamRecordChart?.dispose()
  teamCoreChart?.dispose()
  teamTacticalChart?.dispose()
  teamRadarChart = null
  teamRecordChart = null
  teamCoreChart = null
  teamTacticalChart = null
}

const renderPlayerCharts = async () => {
  await nextTick()
  if (currentPage.value !== 'player-detail' || playerDetailState.value !== 'success' || !playerDetail.value?.basic || isLolGame.value) return
  if (playerAbilityChartRef.value) {
    playerAbilityChart ||= echarts.init(playerAbilityChartRef.value)
    playerAbilityChart.setOption(playerAbilityOption.value, true)
  }
  if (playerMapChartRef.value) {
    playerMapChart ||= echarts.init(playerMapChartRef.value)
    playerMapChart.setOption(playerMapOption.value, true)
  }
  if (playerRatingTrendChartRef.value) {
    playerRatingTrendChart ||= echarts.init(playerRatingTrendChartRef.value)
    playerRatingTrendChart.setOption(playerRatingTrendOption.value, true)
  }
}

const resizePlayerCharts = () => {
  playerAbilityChart?.resize()
  playerMapChart?.resize()
  playerRatingTrendChart?.resize()
}

const disposePlayerCharts = () => {
  playerAbilityChart?.dispose()
  playerMapChart?.dispose()
  playerRatingTrendChart?.dispose()
  playerAbilityChart = null
  playerMapChart = null
  playerRatingTrendChart = null
}

const resizeAllCharts = () => {
  resizeTeamCharts()
  resizePlayerCharts()
}

const disposeAllCharts = () => {
  disposeTeamCharts()
  disposePlayerCharts()
}

const teamMemberMetricValue = (row) => row?.rating || row?.kda || row?.rankScore || row?.position || row?.role || '-'

const playerInitial = (name) => {
  const text = String(name || '').trim()
  return text ? text.charAt(0).toUpperCase() : '?'
}

const equipmentLabelMap = {
  mouse: '鼠标',
  headset: '耳机',
  monitor: '显示器',
  keyboard: '键盘',
  mousepad: '鼠标垫',
  processor: '处理器',
  graphics_card: '显卡',
  chair: '电竞椅',
}

const equipmentLabel = (value) => {
  const key = String(value || '').trim().toLowerCase()
  return equipmentLabelMap[key] || value || '-'
}

const brokenImageMap = ref({})

const imageKeysFor = (value) => {
  const raw = String(value || '').trim()
  if (!raw) return []
  const keys = [raw]
  if (typeof window !== 'undefined') {
    try {
      keys.push(new URL(raw, window.location.href).href)
    } catch (e) {
      // Keep the raw key when URL normalization is unavailable.
    }
  }
  return [...new Set(keys)]
}

const imageKeysFromError = (value) => {
  if (typeof value === 'string') return imageKeysFor(value)
  const target = value?.currentTarget || value?.target
  const keys = [
    ...imageKeysFor(target?.getAttribute?.('src')),
    ...imageKeysFor(target?.currentSrc),
    ...imageKeysFor(target?.src),
  ]
  return [...new Set(keys)]
}

const isBrokenImage = (value) => imageKeysFor(value).some((key) => brokenImageMap.value[key])

const markBrokenImage = (value) => {
  const keys = imageKeysFromError(value)
  if (!keys.length) return
  const next = { ...brokenImageMap.value }
  let changed = false
  for (const key of keys) {
    if (!next[key]) {
      next[key] = true
      changed = true
    }
  }
  if (changed) brokenImageMap.value = next
}

const usableImage = (value) => {
  const src = String(value || '').trim()
  if (!src || isBrokenImage(src)) return ''
  return src
}

const resolveEquipmentLogo = (row) =>
  usableImage(
    row?.logo ||
      row?.logo_url ||
      row?.icon ||
      row?.image ||
      row?.img ||
      '',
  )

const resolveTeammateAvatar = (row) =>
  usableImage(
    row?.portrait ||
      row?.half_portrait ||
      row?.avatar ||
      row?.country_logo ||
      '',
  )

const sanitizeLogo = (value) => {
  const src = String(value || '').trim()
  if (!src || src.endsWith('/null.png')) return ''
  if (isBrokenImage(src)) return ''
  return src
}

const teamLogoByName = computed(() => {
  const map = {}
  for (const row of activeDataset.value?.teams || []) {
    const key = String(row?.name || '').trim()
    const logo = sanitizeLogo(row?.logo || row?.teamLogo || row?.team_logo)
    if (key && logo) map[key] = logo
  }
  return map
})

const croppedLogoMap = ref({})
const cropLoadingMap = ref({})

const isVitalityName = (name) => String(name || '').toLowerCase().includes('vitality')

const isDoubleLogoShape = (w, h) => {
  if (!w || !h) return false
  const ratio = w / h
  return ratio >= 1.95 && ratio <= 2.05
}

const ensureCroppedLogo = (logo, teamName) => {
  const src = sanitizeLogo(logo)
  if (!src) return ''
  if (isValorantGame.value) return src
  const mode = isVitalityName(teamName) ? 'right' : 'left'
  const key = `${src}::${mode}`
  if (croppedLogoMap.value[key]) return croppedLogoMap.value[key]
  if (cropLoadingMap.value[key]) return src
  if (typeof Image === 'undefined') return src

  cropLoadingMap.value = { ...cropLoadingMap.value, [key]: true }

  const mark = (value) => {
    croppedLogoMap.value = { ...croppedLogoMap.value, [key]: value }
    const next = { ...cropLoadingMap.value }
    delete next[key]
    cropLoadingMap.value = next
  }

  const img = new Image()
  img.crossOrigin = 'anonymous'
  img.onload = () => {
    const w = Number(img.naturalWidth || 0)
    const h = Number(img.naturalHeight || 0)
    if (!isDoubleLogoShape(w, h)) {
      mark(src)
      return
    }
    try {
      const halfW = Math.floor(w / 2)
      const square = Math.min(halfW, h)
      const sx = mode === 'right' ? w - halfW : 0
      const canvas = document.createElement('canvas')
      canvas.width = square
      canvas.height = square
      const ctx = canvas.getContext('2d')
      if (!ctx) {
        mark(src)
        return
      }
      ctx.drawImage(img, sx, 0, halfW, h, 0, 0, square, square)
      const out = canvas.toDataURL('image/png')
      mark(out || src)
    } catch (e) {
      mark(src)
    }
  }
  img.onerror = () => mark(src)
  img.src = src
  return src
}

const ACTIVE_MAP_NAME_BY_KEY = Object.freeze({
  dust2: 'Dust2',
  nuke: 'Nuke',
  inferno: 'Inferno',
  anubis: 'Anubis',
  overpass: 'Overpass',
  mirage: 'Mirage',
  ancient: 'Ancient',
})

const ACTIVE_MAP_IMAGE_BY_KEY = Object.freeze({
  dust2: '/images/maps/dust2.jpg',
  nuke: '/images/maps/nuke.jpg',
  inferno: '/images/maps/inferno.jpg',
  anubis: '/images/maps/anubis.jpg',
  overpass: '/images/maps/overpass.jpg',
  mirage: '/images/maps/mirage.jpg',
  ancient: '/images/maps/ancient.jpg',
})

const normalizeMapKey = (value) => {
  const text = String(value || '').trim().toLowerCase()
  if (!text) return ''
  const compact = text.replace(/[^a-z0-9]+/g, '')
  if (!compact) return ''
  if (compact.includes('dust2') || compact.includes('dustii')) return 'dust2'
  for (const key of ['nuke', 'inferno', 'anubis', 'overpass', 'mirage', 'ancient']) {
    if (compact.includes(key)) return key
  }
  return ''
}

const formatMapName = (mapName) => {
  const key = normalizeMapKey(mapName)
  return ACTIVE_MAP_NAME_BY_KEY[key] || String(mapName || '-').trim() || '-'
}

const resolveMapImage = (mapName) => {
  const key = normalizeMapKey(mapName)
  return ACTIVE_MAP_IMAGE_BY_KEY[key] || ''
}

const mapBadgeStyle = (mapName) => {
  const src = resolveMapImage(mapName)
  if (!src) return {}
  return { '--map-bg': `url("${src}")` }
}

const resolveTeamLogo = (teamRow) => {
  const name = String(teamRow?.name || '').trim()
  const src = sanitizeLogo(
    teamRow?.logo || teamRow?.teamLogo || teamRow?.team_logo || teamLogoByName.value[name],
  )
  return ensureCroppedLogo(src, name)
}

const resolveMatchTeamLogo = (matchRow, side) => {
  if (side === 'A') {
    const teamName = String(matchRow?.teamA || '').trim()
    const src = sanitizeLogo(matchRow?.teamALogo || teamLogoByName.value[teamName])
    return ensureCroppedLogo(src, teamName)
  }
  const teamName = String(matchRow?.teamB || '').trim()
  const src = sanitizeLogo(matchRow?.teamBLogo || teamLogoByName.value[teamName])
  return ensureCroppedLogo(src, teamName)
}

const playerTeamLogo = computed(() =>
  ensureCroppedLogo(
    playerDetail.value?.basic?.teamLogo,
    playerDetail.value?.basic?.teamName,
  ),
)

const resolveTeammateTeamLogo = (row) => {
  const teamName = String(
    row?.team_name ||
      row?.team ||
      playerDetail.value?.basic?.teamName ||
      '',
  ).trim()
  const src = sanitizeLogo(
    row?.team_logo ||
      row?.teamLogo ||
      row?.logo ||
      teamLogoByName.value[teamName] ||
      playerDetail.value?.basic?.teamLogo,
  )
  return ensureCroppedLogo(src, teamName)
}

const teamLogoMaskStyle = (logo) => {
  const src = String(logo || '').trim()
  if (!src) return {}
  return { '--team-logo': `url("${src}")` }
}

const toNumber = (value) => {
  const text = String(value ?? '').replace(/[^0-9.+-]/g, '')
  const num = Number.parseFloat(text)
  return Number.isFinite(num) ? num : 0
}

const toIntOrNull = (value) => {
  const num = Number.parseInt(String(value ?? '').trim(), 10)
  return Number.isFinite(num) ? num : null
}

const localDateText = (date = new Date()) => {
  const y = date.getFullYear()
  const m = String(date.getMonth() + 1).padStart(2, '0')
  const d = String(date.getDate()).padStart(2, '0')
  return `${y}-${m}-${d}`
}

const scheduleDateMax = computed(() => localDateText(new Date()))
const scheduleRowsState = ref('idle')
const scheduleRowsError = ref('')
const scheduleRowsFromDb = ref([])
const scheduleRowsHasMore = ref(false)
const scheduleRowsLoadingMore = ref(false)

const normalizeDateFilter = (value) => {
  const text = String(value || '').trim()
  if (!text) return ''
  if (!/^\d{4}-\d{2}-\d{2}$/.test(text)) return ''
  if (text < scheduleDateMin) return scheduleDateMin
  if (text > scheduleDateMax.value) return scheduleDateMax.value
  return text
}

const parseDateText = (value) => {
  const text = String(value || '').trim()
  if (!/^\d{4}-\d{2}-\d{2}$/.test(text)) return Number.NaN
  const ms = Date.parse(`${text}T00:00:00`)
  return Number.isFinite(ms) ? ms : Number.NaN
}

const parseMatchTime = (value) => {
  const text = String(value || '').trim()
  if (!text || text === '-') return Number.NaN
  const normalized = text.replace(' ', 'T')
  const ms = Date.parse(normalized)
  return Number.isFinite(ms) ? ms : Number.NaN
}

const normalizeTierText = (value) =>
  String(value || '')
    .trim()
    .toUpperCase()
    .replace(/\s+/g, '')

const normalizeTournamentName = (value) =>
  String(value || '')
    .trim()
    .toLowerCase()
    .replace(/\s+/g, ' ')

const normalizeTournamentMatchKey = (value) =>
  normalizeTournamentName(value)
    .replace(/\s+/g, '')
    .replace(/[\-_:：·,，.。()（）\[\]【】]/g, '')

const tournamentNameFromRow = (row) => String(row?.name || row?.event_name || row?.tournament || '').trim()
const tournamentRouteKey = (row) => normalizeTournamentName(tournamentNameFromRow(row))
const isSameTournamentName = (a, b) => {
  const left = normalizeTournamentName(a)
  const right = normalizeTournamentName(b)
  if (!left || !right) return false
  if (left === right) return true
  const leftMatchKey = normalizeTournamentMatchKey(left)
  const rightMatchKey = normalizeTournamentMatchKey(right)
  return Boolean(leftMatchKey && rightMatchKey && leftMatchKey === rightMatchKey)
}

const parseTierRank = (value) => {
  const text = normalizeTierText(value)
  if (!text) return null

  const numeric = Number.parseInt(text, 10)
  if (Number.isFinite(numeric)) {
    if (numeric <= 1) return 5
    if (numeric === 2) return 4
    if (numeric === 3) return 3
    if (numeric === 4) return 2
    return 1
  }

  if (text.includes('MAJOR')) return 5
  if (text.includes('S+')) return 5
  if (text.includes('STIER') || text.includes('S-TIER') || text.includes('S级') || text.includes('S級')) return 4
  if (text === 'S' || text.includes('S赛事')) return 4
  if (text.includes('ATIER') || text.includes('A-TIER') || text.includes('A级') || text.includes('A級')) return 3
  if (text === 'A' || text.includes('A赛事')) return 3
  if (text.includes('BTIER') || text.includes('B-TIER') || text.includes('B级') || text.includes('B級')) return 2
  if (text === 'B' || text.includes('B赛事')) return 2
  if (text.includes('CTIER') || text.includes('C-TIER') || text.includes('C级') || text.includes('C級')) return 1
  if (text === 'C' || text.includes('C赛事')) return 1

  return null
}

const tournamentTierByName = computed(() => {
  const map = new Map()
  const normalizedRows = []
  for (const row of activeDataset.value?.tournaments || []) {
    const name = row?.name
    const key = normalizeTournamentName(name)
    const matchKey = normalizeTournamentMatchKey(name)
    if (!key || !matchKey) continue
    const tierText = String(row?.tier || row?.grade || row?.tierLevel || '').trim()
    if (!tierText || tierText === '-') continue
    if (!map.has(key)) map.set(key, tierText)
    if (!map.has(matchKey)) map.set(matchKey, tierText)
    normalizedRows.push({ key: matchKey, tierText })
  }
  map.set('__normalizedRows', normalizedRows.sort((a, b) => b.key.length - a.key.length))
  return map
})

const resolveRowTierText = (row) => {
  const directTier = String(
    row?.tier ||
      row?.grade ||
      row?.tierLevel ||
      row?.tournamentTier ||
      row?.tournament_grade_label ||
      row?.tournamentGradeLabel ||
      '',
  ).trim()
  if (directTier && directTier !== '-') return directTier

  const tournamentName = row?.tournament || row?.event_name || row?.name
  const byExactTournament = tournamentTierByName.value.get(normalizeTournamentName(tournamentName))
  if (byExactTournament) return String(byExactTournament).trim()

  const matchKey = normalizeTournamentMatchKey(tournamentName)
  const byNormalizedTournament = tournamentTierByName.value.get(matchKey)
  if (byNormalizedTournament) return String(byNormalizedTournament).trim()

  const normalizedRows = tournamentTierByName.value.get('__normalizedRows') || []
  const fuzzyMatch = normalizedRows.find((item) => matchKey.includes(item.key) || item.key.includes(matchKey))
  return String(fuzzyMatch?.tierText || '').trim()
}

const passScheduleTierFilter = (row) => {
  if (scheduleTierFilter.value === 'all') return true
  const minRank = scheduleTierThreshold[scheduleTierFilter.value]
  if (!Number.isFinite(minRank)) return true

  const rank = parseTierRank(resolveRowTierText(row))
  if (!Number.isFinite(rank)) return true
  return rank >= minRank
}

const resolveMatchDateText = (row) => {
  const date = String(row?.date || '').trim()
  if (/^\d{4}-\d{2}-\d{2}$/.test(date)) return date
  const matchTime = String(row?.matchTime || '').trim()
  if (/^\d{4}-\d{2}-\d{2}/.test(matchTime)) return matchTime.slice(0, 10)
  return '-'
}

const resolveMatchTimeMs = (row) => {
  const fromMatchTime = parseMatchTime(row?.matchTime)
  if (Number.isFinite(fromMatchTime)) return fromMatchTime
  return parseDateText(resolveMatchDateText(row))
}

const resolveMatchKickoffTime = (row) => {
  const matchTime = String(row?.matchTime || '').trim()
  const compactMatch = matchTime.replace(/\s+/g, ' ')
  if (/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}/.test(compactMatch)) {
    return compactMatch.slice(11, 16)
  }
  if (/^\d{2}:\d{2}(:\d{2})?$/.test(compactMatch)) {
    return compactMatch.slice(0, 5)
  }

  const ms = resolveMatchTimeMs(row)
  if (!Number.isFinite(ms)) return '--:--'
  const dt = new Date(ms)
  const hh = String(dt.getHours()).padStart(2, '0')
  const mm = String(dt.getMinutes()).padStart(2, '0')
  return `${hh}:${mm}`
}

const parseScorePair = (value) => {
  const text = String(value || '').trim()
  const m = text.match(/^(\d+)\s*[-:]\s*(\d+)$/)
  if (!m) return null
  return { left: Number(m[1]), right: Number(m[2]) }
}

const normalizeTeamText = (value) => String(value || '').trim().toLowerCase()
const isTbdTeamName = (value) => {
  const text = String(value || '').trim()
  if (!text || text === '-' || text === '—') return true
  const normalized = text.toLowerCase()
  return ['tbd', '待定', '待定队伍', 'unknown', '未知', 'to be decided'].some((item) => normalized.includes(item))
}
const resolveMatchNamedTeamScore = (row) => [row?.teamA, row?.teamB].filter((name) => !isTbdTeamName(name)).length
const resolveScheduleTierRank = (row) => parseTierRank(resolveRowTierText(row)) || 0

const isFinishedMatch = (row, nowMs = Date.now()) => {
  const statusCode = toIntOrNull(row?.statusCode)
  const noteText = String(row?.note || row?.rawNote || '').trim()
  const hasDetailNote = noteText && noteText !== '-'
  if (statusCode === 2) return true
  if (statusCode === 1) return false

  const scorePair = parseScorePair(row?.score)
  const timeMs = resolveMatchTimeMs(row)

  if (statusCode === 0) {
    if (scorePair && (scorePair.left > 0 || scorePair.right > 0)) return true
    if (hasDetailNote) return true
    if (Number.isFinite(timeMs) && timeMs < nowMs - 12 * 60 * 60 * 1000) return true
    return false
  }

  const winner = String(row?.winner || '').trim()
  if (winner && winner !== '-') return true

  if (scorePair && (scorePair.left > 0 || scorePair.right > 0)) return true
  if (hasDetailNote) return true
  if (!scorePair && Number.isFinite(timeMs) && timeMs < nowMs - 2 * 60 * 60 * 1000 && statusCode === 2) return true
  return false
}

const formatScheduleScore = (row) => {
  const scorePair = parseScorePair(row?.score)
  if (!scorePair) return '-:-'
  const statusCode = toIntOrNull(row?.statusCode)
  if (statusCode === 0 && scorePair.left === 0 && scorePair.right === 0) {
    return '-:-'
  }
  return `${scorePair.left}:${scorePair.right}`
}

const resolveScheduleScorePart = (row, side) => {
  const scorePair = parseScorePair(row?.score)
  if (!scorePair) return '-'
  const statusCode = toIntOrNull(row?.statusCode)
  if (statusCode === 0 && scorePair.left === 0 && scorePair.right === 0) return '-'
  if (side === 'A') return String(scorePair.left)
  return String(scorePair.right)
}

const resolveWinnerSide = (row) => {
  if (!isFinishedMatch(row)) return ''
  const winner = normalizeTeamText(row?.winner)
  const teamA = normalizeTeamText(row?.teamA)
  const teamB = normalizeTeamText(row?.teamB)

  if (winner && winner !== '-') {
    if (teamA && winner === teamA) return 'A'
    if (teamB && winner === teamB) return 'B'
  }

  const scorePair = parseScorePair(row?.score)
  if (!scorePair) return ''
  if (scorePair.left > scorePair.right) return 'A'
  if (scorePair.right > scorePair.left) return 'B'
  return ''
}

const isResultWinner = (row, side) =>
  scheduleViewMode.value === 'result' && resolveWinnerSide(row) === side

const isResultLoser = (row, side) => {
  if (scheduleViewMode.value !== 'result') return false
  const winnerSide = resolveWinnerSide(row)
  if (!winnerSide) return false
  return winnerSide !== side
}

const isMatchSideWinner = (row, side) => resolveWinnerSide(row) === side
const isMatchSideLoser = (row, side) => {
  const winnerSide = resolveWinnerSide(row)
  if (!winnerSide) return false
  return winnerSide !== side
}

const resolveScheduleStatusText = (row) => {
  if (isFinishedMatch(row)) return '已完赛'
  const statusCode = toIntOrNull(row?.statusCode)
  if (statusCode === 1) return '进行中'
  if (statusCode === 0) return '未开赛'
  const timeMs = resolveMatchTimeMs(row)
  if (Number.isFinite(timeMs)) {
    return timeMs > Date.now() ? '未开赛' : '进行中'
  }
  return '-'
}

const clamp = (value, min, max) => Math.min(max, Math.max(min, value))

const displayValue = (value) => {
  if (value === null || value === undefined) return '-'
  const text = String(value).trim()
  return text || '-'
}

const summaryCards = computed(() => {
  const summary = playerDetail.value?.summary || {}
  if (isLolGame.value) {
    return [
      {
        key: 'games_played',
        label: '比赛局数',
        value: displayValue(summary.games_played || playerDetail.value?.basic?.gamesPlayed),
        percent: 100,
        hint: `KDA ${displayValue(summary.kda || playerDetail.value?.basic?.kda || playerDetail.value?.basic?.rating)}`,
      },
      {
        key: 'kda',
        label: 'KDA',
        value: displayValue(summary.kda || playerDetail.value?.basic?.kda || playerDetail.value?.basic?.rating),
        percent: clamp(toNumber(summary.kda || playerDetail.value?.basic?.kda || playerDetail.value?.basic?.rating) * 12, 0, 100),
        hint: `${displayValue(summary.kills)} / ${displayValue(summary.deaths)} / ${displayValue(summary.assists)}`,
      },
      {
        key: 'avg_cs',
        label: '场均补刀',
        value: displayValue(summary.avgCs),
        percent: clamp(toNumber(summary.avgCs) / 4, 0, 100),
        hint: '来自 lol_game_player_stats',
      },
      {
        key: 'kills',
        label: '总击杀',
        value: displayValue(summary.kills),
        percent: clamp(toNumber(summary.kills), 0, 100),
        hint: `助攻 ${displayValue(summary.assists)}`,
      },
    ]
  }

  const mapTotal = toNumber(summary.map_total)
  const matchTotal = toNumber(summary.match_total)
  const mapWinRate = clamp(toNumber(summary.map_win_rate), 0, 100)
  const matchWinRate = clamp(toNumber(summary.match_win_rate), 0, 100)
  const maxCount = Math.max(mapTotal, matchTotal, 1)

  return [
    {
      key: 'map_total',
      label: '地图总数',
      value: displayValue(summary.map_total),
      percent: clamp((mapTotal / maxCount) * 100, 0, 100),
      hint: `MVP ${displayValue(summary.map_mvp_count || summary.map_mvp)}`,
    },
    {
      key: 'map_win_rate',
      label: '地图胜率',
      value: displayValue(summary.map_win_rate),
      percent: mapWinRate,
      hint: `胜 ${displayValue(summary.map_win)} / 负 ${displayValue(summary.map_loss)}`,
    },
    {
      key: 'match_total',
      label: '比赛总数',
      value: displayValue(summary.match_total),
      percent: clamp((matchTotal / maxCount) * 100, 0, 100),
      hint: `MVP ${displayValue(summary.match_mvp_count)}`,
    },
    {
      key: 'match_win_rate',
      label: '比赛胜率',
      value: displayValue(summary.match_win_rate),
      percent: matchWinRate,
      hint: `胜 ${displayValue(summary.match_win)} / 负 ${displayValue(summary.match_loss)}`,
    },
  ]
})

const abilityMetricOrder = computed(() =>
  isLolGame.value
    ? ['wr', 'kda', 'kp', 'kill%', 'cs%', 'dth%', 'pool', 'avg kda']
    : isValorantGame.value
      ? ['rating', 'adr', 'kast', 'impact', 'kpr', 'fdpr', 'swing', 'hs%']
      : ['rating', 'adr', 'kast', 'impact', 'kpr', 'dpr', 'swing'],
)
const abilityMetricLabel = (metric) => String(metric || '').trim().toUpperCase() || '-'
const abilityMetricKey = (metric) => String(metric || '').trim().toLowerCase()
const metricFlagEnabled = (value) =>
  ['1', 'true', 'yes', 'y', 'on'].includes(String(value ?? '').trim().toLowerCase())

const normalizeMetricValue = (value) => {
  const text = String(value ?? '').trim()
  if (!text) return Number.NaN
  const match = text.match(/-?\d+(\.\d+)?/)
  return match ? Number.parseFloat(match[0]) : Number.NaN
}

const abilityMetricRatio = (row) => {
  const value = normalizeMetricValue(row?.value)
  if (!Number.isFinite(value)) return 0

  const candidates = [
    normalizeMetricValue(row?.bad_start),
    normalizeMetricValue(row?.bad_end),
    normalizeMetricValue(row?.middle_start),
    normalizeMetricValue(row?.middle_end),
    normalizeMetricValue(row?.good_start),
    normalizeMetricValue(row?.good_end),
  ].filter((num) => Number.isFinite(num))

  let ratio = 0.5
  if (candidates.length >= 2) {
    const min = Math.min(...candidates)
    const max = Math.max(...candidates)
    if (max > min) {
      const lowerBetter = metricFlagEnabled(row?.lower_better)
      ratio = lowerBetter ? (max - value) / (max - min) : (value - min) / (max - min)
    }
  } else {
    const avg = normalizeMetricValue(row?.avg_value)
    if (Number.isFinite(avg) && avg !== 0) {
      const lowerBetter = metricFlagEnabled(row?.lower_better)
      ratio = lowerBetter ? avg / Math.max(value, 1e-6) : value / avg
      ratio = ratio / 2
    }
  }
  return clamp(ratio * 100, 0, 100)
}

const orderedAbilityMetrics = computed(() => {
  const source = Array.isArray(playerDetail.value?.performanceMetrics)
    ? playerDetail.value.performanceMetrics
    : []
  if (!source.length) return []

  const byMetric = new Map()
  for (const row of source) {
    const key = String(row?.metric || '').trim().toLowerCase()
    if (!key || byMetric.has(key)) continue
    byMetric.set(key, row)
  }

  const ordered = []
  for (const key of abilityMetricOrder.value) {
    if (byMetric.has(key)) {
      ordered.push(byMetric.get(key))
      byMetric.delete(key)
    }
  }
  for (const row of byMetric.values()) ordered.push(row)
  return ordered
})

const abilityMetricsTop = computed(() => orderedAbilityMetrics.value.slice(0, 4))
const abilityMetricsBottom = computed(() => orderedAbilityMetrics.value.slice(4, 8))

const playerIdentityChips = computed(() => {
  const basic = playerDetail.value?.basic || {}
  return [
    { label: '国家/地区', value: displayValue(basic.countryZh || basic.countryEn) },
    { label: '生日', value: displayValue(basic.birthday) },
    { label: 'Top20', value: displayValue(basic.top20_num || basic.top20Num) },
    { label: '奖金', value: displayValue(basic.bonus) },
    { label: '地图', value: displayValue(basic.maps_played || basic.mapsPlayed) },
    { label: '回合', value: displayValue(basic.rounds_played || basic.roundsPlayed) },
  ].filter((item) => item.value !== '-')
})

const playerProfileStats = computed(() => {
  const basic = playerDetail.value?.basic || {}
  return [
    { label: '总击杀', value: displayValue(basic.kills) },
    { label: '总死亡', value: displayValue(basic.deaths) },
    { label: 'KPR', value: displayValue(basic.kpr) },
    { label: 'DPR', value: displayValue(basic.dpr) },
    { label: 'KAST', value: displayValue(basic.kast) },
    { label: '爆头率', value: displayValue(basic.headShot || basic.headshot || basic.hs) },
  ].filter((item) => item.value !== '-')
})

const playerCoreMetrics = computed(() => {
  const basic = playerDetail.value?.basic || {}
  const fallback = [
    { metric: 'rating', value: basic.rating, avg_value: '1.0', good_end: '1.4' },
    { metric: 'adr', value: basic.adr, avg_value: '75', good_end: '110' },
    { metric: 'kast', value: basic.kast, avg_value: '70%', good_end: '85%' },
    { metric: 'impact', value: basic.impact, avg_value: '1.0', good_end: '1.4' },
    { metric: 'kpr', value: basic.kpr, avg_value: '0.7', good_end: '0.95' },
    { metric: 'dpr', value: basic.dpr, avg_value: '0.7', good_end: '0.45', lower_better: '1' },
    { metric: 'hs%', value: basic.headShot || basic.headshot || basic.hs, avg_value: '50%', good_end: '70%' },
  ].filter((row) => displayValue(row.value) !== '-')
  return orderedAbilityMetrics.value.length ? orderedAbilityMetrics.value.slice(0, 7) : fallback
})

const playerMapRows = computed(() =>
  (playerDetail.value?.maps || [])
    .map((row) => ({
      ...row,
      label: formatMapName(row.map_name || row.mapName),
      kd: toNumber(row.map_kd || row.mapKd || row.kd),
      rating: toNumber(row.map_rating || row.mapRating || row.rating),
      useNum: toNumber(row.use_num || row.useNum || row.maps || row.matches),
    }))
    .filter((row) => row.label && (row.kd || row.rating || row.useNum))
    .slice(0, 8),
)

const playerMapSummaryStats = computed(() => {
  const rows = playerMapRows.value
  if (!rows.length) return []
  const mostUsed = [...rows].sort((a, b) => b.useNum - a.useNum)[0]
  const maxUseNum = Math.max(...rows.map((row) => row.useNum), 0)
  const minSample = Math.max(3, Math.ceil(maxUseNum * 0.35))
  const eligibleRows = rows.filter((row) => row.useNum >= minSample)
  const bestRating = [...(eligibleRows.length ? eligibleRows : rows)].sort((a, b) => b.rating - a.rating || b.useNum - a.useNum)[0]
  const avgRating = rows.reduce((sum, row) => sum + row.rating, 0) / rows.length
  return [
    { label: '稳定最佳', value: `${bestRating.label} ${bestRating.rating.toFixed(2)}` },
    { label: '常用地图', value: `${mostUsed.label} ${Math.round(mostUsed.useNum)} 场` },
    { label: '样本门槛', value: `≥ ${minSample} 场` },
    { label: '平均 Rating', value: avgRating.toFixed(2) },
  ]
})

const playerDeviceGroups = computed(() => {
  const mouse = playerDetail.value?.mouseConfig || {}
  const monitor = playerDetail.value?.monitorConfig || {}
  return [
    {
      key: 'mouse',
      title: '鼠标设置',
      rows: [
        { label: '鼠标', value: displayValue(mouse.mouse_name || mouse.name) },
        { label: 'DPI', value: displayValue(mouse.dpi) },
        { label: 'eDPI', value: displayValue(mouse.e_dpi || mouse.edpi) },
        { label: '灵敏度', value: displayValue(mouse.sensitivity) },
        { label: '开镜灵敏度', value: displayValue(mouse.zoom_sensitivity || mouse.zoomSensitivity) },
      ].filter((item) => item.value !== '-'),
    },
    {
      key: 'monitor',
      title: '显示设置',
      rows: [
        { label: '显示器', value: displayValue(monitor.monitor_name || monitor.name) },
        { label: '分辨率', value: displayValue(monitor.resolution) },
        { label: '刷新率', value: displayValue(monitor.hz || monitor.refresh_rate || monitor.refreshRate) },
        { label: '纵横比', value: displayValue(monitor.aspect_ratio || monitor.aspectRatio) },
        { label: '缩放模式', value: displayValue(monitor.scaling_mode || monitor.scalingMode) },
      ].filter((item) => item.value !== '-'),
    },
  ].filter((group) => group.rows.length)
})

const playerRecentRows = computed(() => (playerDetail.value?.recentMatches || []).slice(0, 12))
const animatedAbilityRatioMap = ref({})
let abilityAnimationFrame = 0

const metricDecimals = (value) => {
  const text = String(value ?? '').trim()
  const dot = text.indexOf('.')
  if (dot < 0) return 0
  const fraction = text.slice(dot + 1).match(/^\d+/)
  return fraction ? fraction[0].length : 0
}

const metricHasPercent = (row) => {
  const valueText = String(row?.value ?? '')
  const avgText = String(row?.avg_value ?? '')
  return valueText.includes('%') || avgText.includes('%')
}

const metricAvgText = (row) => {
  const avg = normalizeMetricValue(row?.avg_value)
  if (!Number.isFinite(avg)) return '平均值：-'
  const suffix = metricHasPercent(row) ? '%' : ''
  const digits = Math.max(metricDecimals(row?.value), metricDecimals(row?.avg_value), 1)
  return `平均值：${avg.toFixed(digits)}${suffix}`
}

const metricDeltaInfo = (row) => {
  const value = normalizeMetricValue(row?.value)
  const avg = normalizeMetricValue(row?.avg_value)
  if (!Number.isFinite(value) || !Number.isFinite(avg)) {
    return { text: '与平均差值：-', cls: 'neutral' }
  }
  const delta = value - avg
  const abs = Math.abs(delta)
  const suffix = metricHasPercent(row) ? '%' : ''
  const digits = Math.max(metricDecimals(row?.value), metricDecimals(row?.avg_value), 1)
  const lowerBetter = metricFlagEnabled(row?.lower_better)
  if (delta > 0) {
    return { text: `高于平均 +${abs.toFixed(digits)}${suffix}`, cls: lowerBetter ? 'down' : 'up' }
  }
  if (delta < 0) {
    return { text: `低于平均 -${abs.toFixed(digits)}${suffix}`, cls: lowerBetter ? 'up' : 'down' }
  }
  return { text: `与平均持平 0${suffix}`, cls: 'neutral' }
}

const abilityRingStyle = (row) => {
  const key = abilityMetricKey(row?.metric)
  const ratio = Number(animatedAbilityRatioMap.value[key] ?? 0)
  return { '--ability-value': clamp(ratio, 0, 100) }
}

watch(
  orderedAbilityMetrics,
  (rows) => {
    if (abilityAnimationFrame) cancelAnimationFrame(abilityAnimationFrame)

    const targets = {}
    const seed = {}
    rows.forEach((row) => {
      const key = abilityMetricKey(row?.metric)
      targets[key] = abilityMetricRatio(row)
      seed[key] = 0
    })
    animatedAbilityRatioMap.value = seed

    const duration = 1100
    let startTime = 0
    const animate = (timestamp) => {
      if (!startTime) startTime = timestamp
      const progress = clamp((timestamp - startTime) / duration, 0, 1)
      const eased = 1 - Math.pow(1 - progress, 3)
      const next = {}
      rows.forEach((row) => {
        const key = abilityMetricKey(row?.metric)
        next[key] = targets[key] * eased
      })
      animatedAbilityRatioMap.value = next

      if (progress < 1) {
        abilityAnimationFrame = requestAnimationFrame(animate)
      } else {
        abilityAnimationFrame = 0
      }
    }

    abilityAnimationFrame = requestAnimationFrame(animate)
  },
  { immediate: true },
)

onBeforeUnmount(() => {
  if (abilityAnimationFrame) cancelAnimationFrame(abilityAnimationFrame)
})

const ratingSeries = computed(() =>
  (playerDetail.value?.ratingChart || [])
    .slice(-60)
    .map((row) => ({
      date: String(row.date || ''),
      value: toNumber(row.rate),
    }))
    .filter((row) => row.date && Number.isFinite(row.value) && row.value > 0),
)

const ratingTrendSummary = computed(() => {
  const rows = ratingSeries.value
  if (!rows.length) return []
  const values = rows.map((row) => row.value)
  const avg = values.reduce((sum, value) => sum + value, 0) / values.length
  const high = Math.max(...values)
  const low = Math.min(...values)
  const first = values[0]
  const last = values[values.length - 1]
  const delta = last - first
  return [
    { label: '当前 Rating', value: last.toFixed(2), tone: delta >= 0 ? 'up' : 'down' },
    { label: '区间均值', value: avg.toFixed(2), tone: 'neutral' },
    { label: '最高 / 最低', value: `${high.toFixed(2)} / ${low.toFixed(2)}`, tone: 'neutral' },
    { label: '趋势变化', value: `${delta >= 0 ? '+' : ''}${delta.toFixed(2)}`, tone: delta >= 0 ? 'up' : 'down' },
  ]
})

const ratingTrendText = computed(() => {
  const rows = ratingSeries.value
  if (rows.length < 2) return '样本不足，暂无趋势判断。'
  const first = rows[0].value
  const last = rows[rows.length - 1].value
  const delta = last - first
  if (delta > 0.05) return `最近 ${rows.length} 个样本整体上升，Rating 提升 ${delta.toFixed(2)}。`
  if (delta < -0.05) return `最近 ${rows.length} 个样本整体回落，Rating 下降 ${Math.abs(delta).toFixed(2)}。`
  return `最近 ${rows.length} 个样本整体稳定，波动控制在 ${Math.abs(delta).toFixed(2)}。`
})

const playerAbilityOption = computed(() => ({
  backgroundColor: 'transparent',
  tooltip: {
    trigger: 'item',
    formatter: () => playerCoreMetrics.value.map((row) => `${abilityMetricLabel(row.metric)}：${displayValue(row.value)}`).join('<br/>'),
  },
  radar: {
    radius: '66%',
    indicator: playerCoreMetrics.value.map((row) => ({ name: abilityMetricLabel(row.metric), max: 100 })),
    axisName: { color: '#dbe8f7', fontSize: 11 },
    splitLine: { lineStyle: { color: 'rgba(180, 205, 238, 0.2)' } },
    splitArea: { areaStyle: { color: ['rgba(255,255,255,0.035)', 'rgba(255,255,255,0.015)'] } },
    axisLine: { lineStyle: { color: 'rgba(180, 205, 238, 0.18)' } },
  },
  series: [
    {
      type: 'radar',
      data: [
        {
          name: '核心表现',
          value: playerCoreMetrics.value.map((row) => abilityMetricRatio(row)),
          areaStyle: { color: 'rgba(80, 143, 255, 0.24)' },
          lineStyle: { color: '#6ea8ff', width: 2.2 },
          itemStyle: { color: '#ffffff', borderColor: '#6ea8ff', borderWidth: 2 },
        },
      ],
    },
  ],
}))

const playerMapOption = computed(() => ({
  backgroundColor: 'transparent',
  tooltip: {
    trigger: 'axis',
    axisPointer: { type: 'shadow' },
    formatter: (items) => {
      const idx = items?.[0]?.dataIndex ?? 0
      const row = playerMapRows.value[idx]
      return row ? `${row.label}<br/>Rating：${row.rating.toFixed(2)}<br/>KD：${row.kd.toFixed(2)}<br/>场次：${Math.round(row.useNum)}` : ''
    },
  },
  legend: { top: 4, right: 4, itemGap: 14, textStyle: { color: '#9fb2ca' } },
  grid: { left: 14, right: 12, top: 70, bottom: 20, containLabel: true },
  xAxis: {
    type: 'category',
    data: playerMapRows.value.map((row) => row.label),
    axisLabel: { color: '#cfe0f5', interval: 0, fontSize: 11 },
    axisLine: { lineStyle: { color: 'rgba(180, 205, 238, 0.22)' } },
    axisTick: { show: false },
  },
  yAxis: [
    {
      type: 'value',
      name: 'Rating / KD',
      min: 0,
      axisLabel: { color: '#8fa5c1' },
      splitLine: { lineStyle: { color: 'rgba(180, 205, 238, 0.1)' } },
      nameTextStyle: { color: '#8fa5c1' },
    },
    {
      type: 'value',
      name: '场次',
      min: 0,
      axisLabel: { color: '#8fa5c1' },
      splitLine: { show: false },
      nameTextStyle: { color: '#8fa5c1' },
    },
  ],
  series: [
    {
      name: 'Rating',
      type: 'bar',
      barWidth: 18,
      data: playerMapRows.value.map((row) => ({
        value: row.rating,
        itemStyle: { color: '#6ea8ff', borderRadius: [7, 7, 2, 2] },
      })),
    },
    {
      name: 'KD',
      type: 'bar',
      barWidth: 18,
      data: playerMapRows.value.map((row) => ({
        value: row.kd,
        itemStyle: { color: '#39d98a', borderRadius: [7, 7, 2, 2] },
      })),
    },
    {
      name: '场次',
      type: 'line',
      yAxisIndex: 1,
      smooth: true,
      symbolSize: 7,
      lineStyle: { color: '#f6c85f', width: 2.4 },
      itemStyle: { color: '#f6c85f' },
      data: playerMapRows.value.map((row) => row.useNum),
    },
  ],
}))

const playerRatingTrendOption = computed(() => {
  const values = ratingSeries.value.map((row) => row.value)
  const min = values.length ? Math.max(0, Math.min(...values) - 0.12) : 0
  const max = values.length ? Math.max(...values) + 0.12 : 2
  const avg = values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : 0
  return {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      formatter: (items) => {
        const item = items?.[0]
        const row = ratingSeries.value[item?.dataIndex]
        return row ? `${row.date}<br/>Rating：${row.value.toFixed(2)}` : ''
      },
    },
    grid: { left: 12, right: 18, top: 26, bottom: 26, containLabel: true },
    xAxis: {
      type: 'category',
      data: ratingSeries.value.map((row) => row.date),
      axisLabel: { color: '#8fa5c1', hideOverlap: true },
      axisLine: { lineStyle: { color: 'rgba(180, 205, 238, 0.22)' } },
      axisTick: { show: false },
    },
    yAxis: {
      type: 'value',
      min,
      max,
      axisLabel: { color: '#8fa5c1' },
      splitLine: { lineStyle: { color: 'rgba(180, 205, 238, 0.1)' } },
    },
    series: [
      {
        name: 'Rating',
        type: 'line',
        smooth: true,
        symbolSize: 7,
        lineStyle: { color: '#6ea8ff', width: 3 },
        itemStyle: { color: '#ffffff', borderColor: '#6ea8ff', borderWidth: 2 },
        areaStyle: {
          color: {
            type: 'linear',
            x: 0,
            y: 0,
            x2: 0,
            y2: 1,
            colorStops: [
              { offset: 0, color: 'rgba(110,168,255,0.34)' },
              { offset: 1, color: 'rgba(110,168,255,0.02)' },
            ],
          },
        },
        markLine: avg
          ? {
              symbol: 'none',
              label: { color: '#f6c85f', formatter: '均值 {c}' },
              lineStyle: { color: '#f6c85f', type: 'dashed' },
              data: [{ yAxis: Number(avg.toFixed(2)) }],
            }
          : undefined,
        data: values,
      },
    ],
  }
})

const onTeammateWheel = (event) => {
  const container = event.currentTarget
  if (!(container instanceof HTMLElement)) return

  const canScrollHorizontally = container.scrollWidth > container.clientWidth + 1
  if (!canScrollHorizontally) return

  const absY = Math.abs(event.deltaY)
  const absX = Math.abs(event.deltaX)
  const delta = absX > absY ? event.deltaX : event.deltaY
  if (!delta) return

  container.scrollLeft += delta

  if (absY > absX) {
    event.preventDefault()
  }
}

const emptyRouteState = (gameId, page = 'home') => ({
  gameId,
  page,
  playerId: '',
  teamKey: '',
  matchId: '',
  tournamentKey: '',
})

const parseRouteFromHash = (hash) => {
  const clean = hash.replace(/^#\/?/, '').trim()
  const segments = clean.split('/').filter(Boolean)
  const gameIdRaw = segments[0]
  const pageRaw = segments[1]
  const gameId = validGameIds.has(gameIdRaw) ? gameIdRaw : gameCatalog[0].id

  if (pageRaw === 'player') {
    const playerId = decodeURIComponent(segments.slice(2).join('/'))
    if (playerId) {
      return { ...emptyRouteState(gameId, 'player-detail'), playerId }
    }
  }

  if (pageRaw === 'team') {
    const teamKey = decodeURIComponent(segments.slice(2).join('/'))
    if (teamKey) {
      return { ...emptyRouteState(gameId, 'team-detail'), teamKey }
    }
  }

  if (pageRaw === 'match') {
    const matchId = decodeURIComponent(segments.slice(2).join('/'))
    if (matchId) {
      return { ...emptyRouteState(gameId, 'match-detail'), matchId }
    }
  }

  if (pageRaw === 'tournament') {
    const tournamentKey = decodeURIComponent(segments.slice(2).join('/'))
    if (tournamentKey) {
      return { ...emptyRouteState(gameId, 'tournament-detail'), tournamentKey }
    }
  }

  return emptyRouteState(gameId, pageKeys.includes(pageRaw) ? pageRaw : 'home')
}

const buildHash = (gameId, page, playerId = '', teamKey = '', matchId = '', tournamentKey = '') => {
  if (page === 'player-detail' && playerId) {
    return `#/${gameId}/player/${encodeURIComponent(playerId)}`
  }
  if (page === 'team-detail' && teamKey) {
    return `#/${gameId}/team/${encodeURIComponent(teamKey)}`
  }
  if (page === 'match-detail' && matchId) {
    return `#/${gameId}/match/${encodeURIComponent(matchId)}`
  }
  if (page === 'tournament-detail' && tournamentKey) {
    return `#/${gameId}/tournament/${encodeURIComponent(tournamentKey)}`
  }
  return `#/${gameId}/${page}`
}

const syncFromHash = () => {
  const { gameId, page, playerId, teamKey, matchId, tournamentKey } = parseRouteFromHash(window.location.hash)
  selectedGameId.value = gameId
  currentPage.value = page
  selectedPlayerId.value = playerId || ''
  selectedTeamKey.value = teamKey || ''
  selectedMatchId.value = matchId || ''
  selectedTournamentKey.value = tournamentKey || ''
}

const navigateTo = (page, options = {}) => {
  isGameMenuOpen.value = false
  const hash = buildHash(
    selectedGameId.value,
    page,
    options.playerId || '',
    options.teamKey || '',
    options.matchId || '',
    options.tournamentKey || '',
  )
  if (window.location.hash !== hash) {
    window.location.hash = hash
  }
}

const toggleGameMenu = () => {
  isGameMenuOpen.value = !isGameMenuOpen.value
}

const selectGame = (gameId) => {
  selectedGameId.value = gameId
  isGameMenuOpen.value = false
}

const handleDocPointerDown = (event) => {
  const root = switchBlockRef.value
  if (!root) return
  if (!root.contains(event.target)) {
    isGameMenuOpen.value = false
  }
}

const handleWindowKeydown = (event) => {
  if (event.key === 'Escape') {
    if (expandedHomePanel.value) {
      closeHomePanel()
      return
    }
    isGameMenuOpen.value = false
  }
}

let tableHoverActiveRow = null

const clearTableHoverState = () => {
  if (tableHoverActiveRow) {
    tableHoverActiveRow.classList.remove('table-row-active')
  }
  tableHoverActiveRow = null
}

const applyTableHoverState = (cell) => {
  const row = cell?.closest?.('.table-row')
  if (!row) {
    clearTableHoverState()
    return
  }

  if (tableHoverActiveRow === row) {
    return
  }

  clearTableHoverState()
  tableHoverActiveRow = row

  row.classList.add('table-row-active')
}

const handleTablePointerOver = (event) => {
  if (!(event.target instanceof Element)) {
    clearTableHoverState()
    return
  }
  const cell = event.target.closest('.table-wrap .table-row > *')
  if (!cell) {
    clearTableHoverState()
    return
  }
  applyTableHoverState(cell)
}

const handleTablePointerLeaveWindow = () => {
  clearTableHoverState()
}

const filteredRows = computed(() => {
  const dataset = activeDataset.value
  const schedule =
    backendDatasetGameIds.has(selectedGameId.value) && currentPage.value === 'schedule'
      ? [...(scheduleRowsFromDb.value || [])]
      : (dataset.matches || []).filter((row) => passScheduleTierFilter(row))
  const tournaments = (dataset.tournaments || []).filter((row) => passScheduleTierFilter(row))
  return {
    schedule,
    tournaments,
    teams: dataset.teams || [],
    players: dataset.players || [],
  }
})

const regionValue = (row) => String(row?.region || row?.area || row?.leagueRegion || '-').trim() || '-'
const regionSortOrder = [
  'CN',
  'Pacific',
  'EMEA',
  'Americas',
  'Game Changers',
  'LCK',
  'LPL',
  'LEC',
  'LTA',
  'LCP',
  'VCS',
  'PCS',
  'CBLOL',
  'LLA',
  'International',
  'Unknown',
  '-',
]
const regionSortRank = (region) => {
  const idx = regionSortOrder.indexOf(region)
  return idx >= 0 ? idx : regionSortOrder.length
}

const lolRegionOptions = computed(() => {
  const dataset = activeDataset.value || {}
  const regions = new Set()
  for (const collection of [dataset.tournaments, dataset.teams, dataset.players]) {
    for (const row of collection || []) {
      const region = regionValue(row)
      if (region && region !== '-') regions.add(region)
    }
  }
  const items = [...regions].sort((a, b) => {
    const ar = regionSortRank(a)
    const br = regionSortRank(b)
    if (ar !== br) return ar - br
    return a.localeCompare(b)
  })
  return [{ value: 'all', label: '全部赛区' }, ...items.map((region) => ({ value: region, label: region }))]
})

const passLolRegionFilter = (row) => {
  if (!isRegionRankGame.value || lolRegionFilter.value === 'all') return true
  return regionValue(row) === lolRegionFilter.value
}

const groupRowsByRegion = (rows, rankKey = 'displayRank') => {
  const groups = new Map()
  for (const row of rows || []) {
    const region = regionValue(row)
    if (!groups.has(region)) groups.set(region, [])
    groups.get(region).push(row)
  }
  return [...groups.entries()]
    .sort(([a], [b]) => {
      const ar = regionSortRank(a)
      const br = regionSortRank(b)
      if (ar !== br) return ar - br
      return a.localeCompare(b)
    })
    .map(([region, rowsInRegion]) => ({
      region,
      rows: rowsInRegion.map((row, idx) => ({ ...row, [rankKey]: idx + 1 })),
    }))
}

const lolTournamentRows = computed(() =>
  (filteredRows.value.tournaments || []).filter((row) => passLolRegionFilter(row)),
)

const lolTournamentGroups = computed(() => groupRowsByRegion(lolTournamentRows.value))

const visibleTournamentRows = computed(() =>
  (filteredRows.value.tournaments || []).slice(0, tournamentVisibleCount.value),
)

const hasMoreTournamentRows = computed(
  () => visibleTournamentRows.value.length < (filteredRows.value.tournaments || []).length,
)

const resetTournamentVisibleRows = () => {
  tournamentVisibleCount.value = TOURNAMENT_PAGE_SIZE
}

const loadMoreTournamentRows = () => {
  if (!hasMoreTournamentRows.value) return
  tournamentVisibleCount.value += TOURNAMENT_PAGE_SIZE
}

const handleTournamentScroll = (event) => {
  const container = event.currentTarget
  if (!(container instanceof HTMLElement)) return
  const nearBottom = container.scrollTop + container.clientHeight >= container.scrollHeight - 24
  if (nearBottom) {
    loadMoreTournamentRows()
  }
}

const resolveScheduleStatusPriority = (row, nowMs = Date.now()) => {
  const statusCode = toIntOrNull(row?.statusCode)
  if (statusCode === 1) return 3
  if (!isFinishedMatch(row, nowMs)) return 2
  return 1
}

const compareScheduleRows = (a, b, nowMs = Date.now()) => {
  const statusDiff = resolveScheduleStatusPriority(b, nowMs) - resolveScheduleStatusPriority(a, nowMs)
  if (statusDiff) return statusDiff

  const ta = resolveMatchTimeMs(a)
  const tb = resolveMatchTimeMs(b)
  const aValid = Number.isFinite(ta)
  const bValid = Number.isFinite(tb)
  if (aValid && bValid) {
    if (scheduleViewMode.value === 'result') {
      if (tb !== ta) return tb - ta
    } else {
      if (ta !== tb) return ta - tb
    }
  } else if (aValid) {
    return -1
  } else if (bValid) {
    return 1
  }

  const ad = resolveMatchDateText(a)
  const bd = resolveMatchDateText(b)
  if (ad !== bd) {
    if (scheduleViewMode.value === 'result') return bd.localeCompare(ad)
    return ad.localeCompare(bd)
  }

  const tierDiff = resolveScheduleTierRank(b) - resolveScheduleTierRank(a)
  if (tierDiff) return tierDiff

  const namedDiff = resolveMatchNamedTeamScore(b) - resolveMatchNamedTeamScore(a)
  if (namedDiff) return namedDiff

  return String(a?.matchId || a?.tournament || a?.teamA || '').localeCompare(String(b?.matchId || b?.tournament || b?.teamA || ''))
}

const filteredScheduleRows = computed(() => {
  const rows = [...(filteredRows.value.schedule || [])]

  const nowMs = Date.now()
  const dateFilter = normalizeDateFilter(scheduleDateFilter.value)

  const visibleRows = rows.filter((row) => {
    const finished = isFinishedMatch(row, nowMs)
    if (scheduleViewMode.value === 'result' && !finished) return false
    if (scheduleViewMode.value === 'fixture' && finished) return false

    if (dateFilter) {
      return resolveMatchDateText(row) === dateFilter
    }
    return true
  })

  visibleRows.sort((a, b) => compareScheduleRows(a, b, nowMs))

  return visibleRows
})

const parseRankValue = (value) => {
  const text = String(value ?? '').trim()
  if (!text || text === '-') return null
  const num = Number.parseInt(text, 10)
  if (!Number.isFinite(num) || num <= 0) return null
  return num
}

const currentTeamRank = (row) => {
  if (teamRankMode.value === 'valve') {
    return row.valveRank ?? row.rank ?? '-'
  }
  return row.hltvRank ?? row.rank ?? '-'
}

const teamRowsWithRanking = computed(() => {
  const rows = [...(activeDataset.value?.leaderboard || [])]
  rows.sort((a, b) => {
    const ar = parseRankValue(currentTeamRank(a)) ?? 99999
    const br = parseRankValue(currentTeamRank(b)) ?? 99999
    if (ar !== br) return ar - br
    return String(a.name || '').localeCompare(String(b.name || ''))
  })
  return rows
})

const lolTeamRows = computed(() => teamRowsWithRanking.value.filter((row) => passLolRegionFilter(row)))
const lolTeamGroups = computed(() => groupRowsByRegion(lolTeamRows.value, 'regionRank'))

const playerRankScoreValue = (row) => {
  const score = toNumber(row?.rankScore)
  if (score > 0) return score
  const kda = toNumber(row?.kda)
  if (kda > 0) return kda
  return toNumber(row?.rating)
}

const sortPlayersByRankScore = (rows) => {
  const list = [...(rows || [])]
  list.sort((a, b) => {
    const sa = playerRankScoreValue(a)
    const sb = playerRankScoreValue(b)
    if (sb !== sa) return sb - sa

    const ra = toNumber(a?.rating)
    const rb = toNumber(b?.rating)
    if (rb !== ra) return rb - ra

    return String(a?.name || '').localeCompare(String(b?.name || ''))
  })
  return list
}

const scheduleTeamById = computed(() => {
  const map = new Map()
  for (const row of [...(activeDataset.value?.teams || []), ...(activeDataset.value?.leaderboard || [])]) {
    const id = String(row?.teamId || row?.team_id || '').trim()
    if (id) map.set(id, row)
  }
  return map
})

const scheduleTeamByName = computed(() => {
  const map = new Map()
  for (const row of [...(activeDataset.value?.teams || []), ...(activeDataset.value?.leaderboard || [])]) {
    const name = normalizeTeamText(row?.name || row?.teamName || row?.team_name)
    if (name) map.set(name, row)
  }
  return map
})

const schedulePlayersByTeam = computed(() => {
  const map = new Map()
  for (const row of activeDataset.value?.players || []) {
    const team = normalizeTeamText(row?.team || row?.teamName || row?.team_name)
    if (!team) continue
    if (!map.has(team)) map.set(team, [])
    map.get(team).push(row)
  }
  return map
})

const resolveMatchTeamRow = (row, side) => {
  const id = String(side === 'A' ? row?.teamAId || row?.team_a_id : row?.teamBId || row?.team_b_id || '').trim()
  if (id && scheduleTeamById.value.has(id)) return scheduleTeamById.value.get(id)
  const name = normalizeTeamText(side === 'A' ? row?.teamA : row?.teamB)
  return scheduleTeamByName.value.get(name) || null
}

const resolveTeamWinRateRaw = (teamRow) => {
  if (!teamRow) return null
  const raw = Number(teamRow.winRateRaw)
  if (Number.isFinite(raw) && raw > 0) return raw > 1 ? clamp(raw / 100, 0, 1) : clamp(raw, 0, 1)
  for (const key of ['winRate', 'mapWinRate']) {
    const value = Number.parseFloat(String(teamRow?.[key] ?? '').replace(/[^0-9.+-]/g, ''))
    if (Number.isFinite(value) && value > 0) return clamp(value / 100, 0, 1)
  }
  return null
}

const resolveTeamPlayerPower = (teamName) => {
  const players = schedulePlayersByTeam.value.get(normalizeTeamText(teamName)) || []
  const scores = sortPlayersByRankScore(players)
    .slice(0, 5)
    .map(playerRankScoreValue)
    .filter((value) => Number.isFinite(value) && value > 0)
  if (!scores.length) return null
  const avg = scores.reduce((sum, value) => sum + value, 0) / scores.length
  if (avg <= 2) return clamp((avg - 0.6) / 1.0, 0, 1)
  if (avg <= 10) return clamp(avg / 10, 0, 1)
  return clamp(avg / 100, 0, 1)
}

const resolveTeamQualityPower = (teamRow) => {
  if (!teamRow) return null
  const rating = toNumber(teamRow.rating || teamRow.kda)
  if (rating > 0) return rating <= 2 ? clamp((rating - 0.6) / 1.0, 0, 1) : clamp(rating / 10, 0, 1)
  const kd = toNumber(teamRow.kd)
  if (kd > 0) return kd <= 2 ? clamp((kd - 0.5) / 1.2, 0, 1) : clamp(kd / 100, 0, 1)
  const rank = parseRankValue(currentTeamRank(teamRow))
  if (rank) return clamp(1 - (rank - 1) / 50, 0, 1)
  return null
}

const resolvePredictionSidePower = (teamRow, teamName) => {
  const winRate = resolveTeamWinRateRaw(teamRow)
  const playerPower = resolveTeamPlayerPower(teamName)
  const qualityPower = resolveTeamQualityPower(teamRow)
  const available = [winRate, playerPower, qualityPower].filter((value) => value !== null)
  if (!available.length) return null
  const strength =
    0.55 * (winRate ?? 0.5) +
    0.35 * (playerPower ?? 0.5) +
    0.10 * (qualityPower ?? 0.5)
  return { strength, sampleCount: available.length }
}

const resolveSchedulePrediction = (row) => {
  if (isTbdTeamName(row?.teamA) || isTbdTeamName(row?.teamB)) {
    return { available: false, label: '预测待定', reason: '队伍未确定' }
  }
  const teamA = resolveMatchTeamRow(row, 'A')
  const teamB = resolveMatchTeamRow(row, 'B')
  const powerA = resolvePredictionSidePower(teamA, row?.teamA)
  const powerB = resolvePredictionSidePower(teamB, row?.teamB)
  if (!powerA || !powerB) {
    return { available: false, label: '预测待定', reason: '队伍数据不足' }
  }
  const diff = powerA.strength - powerB.strength
  const teamAProbability = Math.round(clamp(0.5 + diff * 0.75, 0.18, 0.82) * 100)
  const teamBProbability = 100 - teamAProbability
  const sampleCount = powerA.sampleCount + powerB.sampleCount
  const confidence = sampleCount >= 5 ? '高' : sampleCount >= 4 ? '中' : '低'
  return {
    available: true,
    teamAProbability,
    teamBProbability,
    favoriteSide: teamAProbability >= teamBProbability ? 'A' : 'B',
    confidence,
    reason: '战队胜率 + 选手评分',
  }
}

const playerRowsWithRank = computed(() =>
  sortPlayersByRankScore(filteredRows.value.players || []).map((row, idx) => ({
    ...row,
    displayRank: idx + 1,
  })),
)
const visiblePlayerRowsWithRank = computed(() =>
  playerRowsWithRank.value.slice(0, playerVisibleCount.value),
)

const lolPlayerRows = computed(() =>
  sortPlayersByRankScore(filteredRows.value.players || []).filter((row) => passLolRegionFilter(row)),
)
const visibleLolPlayerRows = computed(() => lolPlayerRows.value.slice(0, playerVisibleCount.value))
const lolPlayerGroups = computed(() => groupRowsByRegion(visibleLolPlayerRows.value))
const playerTotalCount = computed(() => (isRegionRankGame.value ? lolPlayerRows.value.length : playerRowsWithRank.value.length))
const visiblePlayerCount = computed(() =>
  Math.min(playerVisibleCount.value, playerTotalCount.value),
)
const hasMorePlayerRows = computed(() => visiblePlayerCount.value < playerTotalCount.value)

const resetPlayerVisibleRows = () => {
  playerVisibleCount.value = PLAYER_PAGE_SIZE
}

const loadMorePlayerRows = () => {
  if (!hasMorePlayerRows.value) return
  playerVisibleCount.value += PLAYER_PAGE_SIZE
}

const handlePlayerScroll = (event) => {
  const container = event.currentTarget
  if (!(container instanceof HTMLElement)) return
  const nearBottom = container.scrollTop + container.clientHeight >= container.scrollHeight - 24
  if (nearBottom) {
    loadMorePlayerRows()
  }
}

const homeSearchText = computed(() => String(searchKeyword.value || '').trim())

const includesSearchText = (values, searchTextLower) =>
  values.some((value) => String(value ?? '').toLowerCase().includes(searchTextLower))

const homeSearchResults = computed(() => {
  const k = homeSearchText.value.toLowerCase()
  if (!k) {
    return { teams: [], players: [], series: [], total: 0 }
  }

  const teamRows = (activeDataset.value?.teams || []).filter((row) =>
    includesSearchText([row.name, row.region, row.style, row.form], k),
  )
  const playerRows = (activeDataset.value?.players || []).filter((row) =>
    includesSearchText([row.name, row.playerId, row.team, row.role, row.rating, row.highlight], k),
  )
  const seriesRows = (activeDataset.value?.matches || []).filter((row) =>
    includesSearchText([row.tournament, row.teamA, row.teamB, row.stage, row.score, row.winner], k),
  )

  const total = teamRows.length + playerRows.length + seriesRows.length
  return {
    teams: teamRows.slice(0, 6),
    players: playerRows.slice(0, 6),
    series: seriesRows.slice(0, 8),
    total,
  }
})

const openSearchResult = (kind, row) => {
  if (kind === 'player' && row?.playerId) {
    navigateTo('player-detail', { playerId: row.playerId })
    return
  }
  if (kind === 'team') {
    const teamKey = String(row?.teamId || row?.team_id || row?.name || '').trim()
    if (teamKey) navigateTo('team-detail', { teamKey })
    return
  }
  if (kind === 'series') {
    navigateTo('schedule')
  }
}

const normalizeRegionKey = (value) => String(value || '').trim().toLowerCase()
const isCnValorantPlayer = (row) => normalizeRegionKey(regionValue(row)) === 'cn'
const isPacificValorantPlayer = (row) => {
  const region = normalizeRegionKey(regionValue(row))
  return ['pacific', 'apac', 'asia', 'sea', 'kr', 'jp', 'id', 'ph', 'th', 'vn', 'sg', 'my', 'mn', 'oce'].some(
    (key) => region === key || region.includes(key),
  )
}

const pushUniquePlayers = (target, rows, limit) => {
  const seen = new Set(target.map((row) => String(row?.playerId || row?.name || '').trim()).filter(Boolean))
  for (const row of rows) {
    if (target.length >= limit) break
    const key = String(row?.playerId || row?.name || '').trim()
    if (!key || seen.has(key)) continue
    target.push(row)
    seen.add(key)
  }
}

const valorantHomePlayerSample = (rows) => {
  const selected = []
  const cnRows = rows.filter(isCnValorantPlayer)
  const pacificRows = rows.filter((row) => !isCnValorantPlayer(row) && isPacificValorantPlayer(row))
  const otherRows = rows.filter((row) => {
    const region = normalizeRegionKey(regionValue(row))
    return region && region !== '-' && !isCnValorantPlayer(row) && !isPacificValorantPlayer(row)
  })

  pushUniquePlayers(selected, cnRows, 3)
  pushUniquePlayers(selected, pacificRows, 5)
  pushUniquePlayers(selected, otherRows, 6)
  pushUniquePlayers(selected, [...cnRows, ...pacificRows], 6)
  pushUniquePlayers(selected, rows, 6)
  return selected
}

const homeTopPlayers = computed(() => {
  const rows = sortPlayersByRankScore(activeDataset.value?.players || [])
  if (isValorantGame.value) {
    return valorantHomePlayerSample(rows).slice(0, 12)
  }
  return rows.slice(0, 12)
})

const homeTopRanking = computed(() => {
  const rows = activeDataset.value?.leaderboard || []
  if (isValorantGame.value) {
    const regionalTop = groupRowsByRegion(rows, 'regionRank')
      .flatMap((group) => group.rows.slice(0, 4))
    return regionalTop.length >= 12 ? regionalTop.slice(0, 12) : rows.slice(0, 12)
  }
  return rows.slice(0, 12)
})
const totalValue = (key, fallbackRows) => {
  const raw = activeDataset.value?.totals?.[key]
  const num = Number.parseInt(String(raw ?? ''), 10)
  if (Number.isFinite(num) && num >= 0) return num
  return (fallbackRows || []).length
}
const homeMatchCount = computed(() => totalValue('matches', activeDataset.value?.matches || []))
const homeTeamCount = computed(() => totalValue('teams', activeDataset.value?.teams || []))
const homePlayerCount = computed(() => totalValue('players', activeDataset.value?.players || []))
const homeTournamentCount = computed(() => totalValue('tournaments', activeDataset.value?.tournaments || []))
const homeHasData = computed(() => homeTopRanking.value.length > 0 || homeTopPlayers.value.length > 0)
const homeStatsCards = computed(() => [
  { key: 'tournaments', label: '赛事总数', value: String(homeTournamentCount.value), hint: '覆盖赛事池' },
  { key: 'matches', label: '比赛总数', value: String(homeMatchCount.value), hint: '赛程与赛果' },
  { key: 'teams', label: '战队总数', value: String(homeTeamCount.value), hint: '榜单战队' },
  { key: 'players', label: '选手总数', value: String(homePlayerCount.value), hint: '选手资料库' },
])
const isKnownTeamName = (value) => {
  const text = String(value || '').trim()
  if (!text || text === '-' || text === '—') return false
  const normalized = text.toLowerCase()
  return !['tbd', '待定', '待定队伍', 'unknown', '未知', 'to be decided'].some((item) => normalized.includes(item))
}

const matchKnownTeamCount = (row) => [row?.teamA, row?.teamB].filter(isKnownTeamName).length

const isNamedMatch = (row) => matchKnownTeamCount(row) > 0

const HOME_MATCH_LOOKAHEAD_MS = 30 * 24 * 60 * 60 * 1000
const matchTierRank = (row) => parseTierRank(resolveRowTierText(row)) || 0
const matchTimeDistance = (row, nowMs = Date.now()) => {
  const timeMs = resolveMatchTimeMs(row)
  return Number.isFinite(timeMs) ? Math.abs(timeMs - nowMs) : Number.POSITIVE_INFINITY
}
const isFutureMatchInHomeWindow = (row, nowMs = Date.now()) => {
  const timeMs = resolveMatchTimeMs(row)
  return Number.isFinite(timeMs) && timeMs >= nowMs - 12 * 60 * 60 * 1000 && timeMs <= nowMs + HOME_MATCH_LOOKAHEAD_MS
}
const isHighTierNamedMatch = (row) => isNamedMatch(row) && matchTierRank(row) >= 4

const sortedHomeMatches = computed(() => {
  const nowMs = Date.now()
  return [...(activeDataset.value?.matches || [])]
    .filter(isNamedMatch)
    .sort((a, b) => {
      const inWindowDiff = Number(isFutureMatchInHomeWindow(b, nowMs)) - Number(isFutureMatchInHomeWindow(a, nowMs))
      if (inWindowDiff) return inWindowDiff

      const highTierDiff = Number(isHighTierNamedMatch(b)) - Number(isHighTierNamedMatch(a))
      if (highTierDiff) return highTierDiff

      const tierDiff = matchTierRank(b) - matchTierRank(a)
      if (tierDiff) return tierDiff

      const unfinishedDiff = Number(!isFinishedMatch(b, nowMs)) - Number(!isFinishedMatch(a, nowMs))
      if (unfinishedDiff) return unfinishedDiff

      const teamDiff = matchKnownTeamCount(b) - matchKnownTeamCount(a)
      if (teamDiff) return teamDiff

      return matchTimeDistance(a, nowMs) - matchTimeDistance(b, nowMs)
    })
})

const homeRecentMatches = computed(() => {
  const nowMs = Date.now()
  const inWindowMatches = sortedHomeMatches.value.filter((row) => isFutureMatchInHomeWindow(row, nowMs))
  const highTierMatches = inWindowMatches.filter(isHighTierNamedMatch)
  if (highTierMatches.length >= 6) return highTierMatches.slice(0, 6)
  const fallbackMatches = sortedHomeMatches.value.filter((row) => !highTierMatches.includes(row))
  return [...highTierMatches, ...fallbackMatches].slice(0, 6)
})
const homeUpcomingMatches = computed(() => sortedHomeMatches.value.filter((row) => !isFinishedMatch(row)).slice(0, 4))
const homeFinishedMatches = computed(() => (activeDataset.value?.matches || []).filter((row) => isFinishedMatch(row)).slice(0, 4))
const resolveTournamentTimeMs = (row, key) => {
  const value = row?.[key]
  const matchTime = parseMatchTime(value)
  if (Number.isFinite(matchTime)) return matchTime
  return parseDateText(String(value || '').slice(0, 10))
}
const isTournamentInHomeWindow = (row, nowMs = Date.now()) => {
  const startMs = resolveTournamentTimeMs(row, 'start')
  const endMs = resolveTournamentTimeMs(row, 'end')
  const lowerBound = nowMs - 12 * 60 * 60 * 1000
  const upperBound = nowMs + HOME_MATCH_LOOKAHEAD_MS
  if (Number.isFinite(startMs) && startMs >= lowerBound && startMs <= upperBound) return true
  if (Number.isFinite(endMs) && endMs >= lowerBound && endMs <= upperBound) return true
  if (Number.isFinite(startMs) && Number.isFinite(endMs)) return startMs <= upperBound && endMs >= lowerBound
  return false
}
const tournamentTierRank = (row) => parseTierRank(row?.tier || row?.grade || row?.tierLevel) || 0
const tournamentStartDistance = (row, nowMs = Date.now()) => {
  const startMs = resolveTournamentTimeMs(row, 'start')
  return Number.isFinite(startMs) ? Math.abs(startMs - nowMs) : Number.POSITIVE_INFINITY
}
const homeFeaturedTournaments = computed(() => {
  const nowMs = Date.now()
  const rows = [...(activeDataset.value?.tournaments || [])]
  rows.sort((a, b) => {
    const windowDiff = Number(isTournamentInHomeWindow(b, nowMs)) - Number(isTournamentInHomeWindow(a, nowMs))
    if (windowDiff) return windowDiff
    const highTierDiff = Number(tournamentTierRank(b) >= 4) - Number(tournamentTierRank(a) >= 4)
    if (highTierDiff) return highTierDiff
    const tierDiff = tournamentTierRank(b) - tournamentTierRank(a)
    if (tierDiff) return tierDiff
    return tournamentStartDistance(a, nowMs) - tournamentStartDistance(b, nowMs)
  })
  const inWindowHighTier = rows.filter((row) => isTournamentInHomeWindow(row, nowMs) && tournamentTierRank(row) >= 4)
  if (inWindowHighTier.length >= 6) return inWindowHighTier.slice(0, 6)
  return [...inWindowHighTier, ...rows.filter((row) => !inWindowHighTier.includes(row))].slice(0, 6)
})
const homeHeroMatch = computed(() => homeUpcomingMatches.value[0] || homeRecentMatches.value[0] || null)
const chunkRows = (rows, size) => {
  const chunks = []
  for (let idx = 0; idx < rows.length; idx += size) chunks.push(rows.slice(idx, idx + size))
  return chunks
}

const homeTeamCarouselPages = computed(() => chunkRows(homeTopRanking.value.slice(0, 12), 4))
const homePlayerCarouselPages = computed(() => chunkRows(homeTopPlayers.value.slice(0, 12), 3))
const homeFeaturedTeams = computed(() => homeTeamCarouselPages.value[homeTeamCarouselPage.value] || homeTeamCarouselPages.value[0] || [])
const homeFeaturedPlayers = computed(() => homePlayerCarouselPages.value[homePlayerCarouselPage.value] || homePlayerCarouselPages.value[0] || [])
const homeExpandedTeams = computed(() => homeTopRanking.value.slice(0, 12))
const homeExpandedPlayers = computed(() => homeTopPlayers.value.slice(0, 12))

const stopHomeTeamCarousel = () => {
  if (homeTeamCarouselTimer) {
    clearInterval(homeTeamCarouselTimer)
    homeTeamCarouselTimer = null
  }
}

const stopHomePlayerCarousel = () => {
  if (homePlayerCarouselTimer) {
    clearInterval(homePlayerCarouselTimer)
    homePlayerCarouselTimer = null
  }
}

const startHomeTeamCarousel = () => {
  stopHomeTeamCarousel()
  homeTeamCarouselTimer = setInterval(() => {
    const total = homeTeamCarouselPages.value.length
    if (total > 1 && expandedHomePanel.value !== 'teams') {
      homeTeamCarouselPage.value = (homeTeamCarouselPage.value + 1) % total
    }
  }, 4000)
}

const startHomePlayerCarousel = () => {
  stopHomePlayerCarousel()
  homePlayerCarouselTimer = setInterval(() => {
    const total = homePlayerCarouselPages.value.length
    if (total > 1 && expandedHomePanel.value !== 'players') {
      homePlayerCarouselPage.value = (homePlayerCarouselPage.value + 1) % total
    }
  }, 4000)
}

const pauseHomeCarousel = (type) => {
  if (type === 'teams') stopHomeTeamCarousel()
  if (type === 'players') stopHomePlayerCarousel()
}

const resumeHomeCarousel = (type) => {
  if (expandedHomePanel.value) return
  if (type === 'teams') startHomeTeamCarousel()
  if (type === 'players') startHomePlayerCarousel()
}

const openHomePanel = (type) => {
  expandedHomePanel.value = type
  pauseHomeCarousel(type)
}

const closeHomePanel = () => {
  const previous = expandedHomePanel.value
  expandedHomePanel.value = ''
  if (previous === 'teams') startHomeTeamCarousel()
  if (previous === 'players') startHomePlayerCarousel()
}

const updateHomePanelGlow = (event) => {
  const panel = event.currentTarget
  if (!(panel instanceof HTMLElement)) return
  const rect = panel.getBoundingClientRect()
  panel.style.setProperty('--cursor-x', `${event.clientX - rect.left}px`)
  panel.style.setProperty('--cursor-y', `${event.clientY - rect.top}px`)
}

const clearHomePanelGlow = (event) => {
  const panel = event.currentTarget
  if (!(panel instanceof HTMLElement)) return
  panel.style.removeProperty('--cursor-x')
  panel.style.removeProperty('--cursor-y')
}

watch([selectedGameId, currentPage], () => {
  closeHomePanel()
  homeTeamCarouselPage.value = 0
  homePlayerCarouselPage.value = 0
})

const homeInsightBullets = computed(() => {
  const heroMatch = homeHeroMatch.value
  const topTeam = homeTopRanking.value[0]?.name || '暂无榜首战队'
  const topPlayer = homeTopPlayers.value[0]?.name || '暂无重点选手'
  const matchLine = heroMatch
    ? `近期重点关注 ${heroMatch.teamA || '-'} 对阵 ${heroMatch.teamB || '-'}，所属赛事为 ${heroMatch.tournament || '-'}。`
    : `${activeGameName.value} 暂无可展示的焦点对阵，建议先查看完整赛程。`
  return [
    `${activeGameName.value} 当前整合 ${homeTournamentCount.value} 个赛事、${homeMatchCount.value} 场比赛和 ${homeTeamCount.value} 支战队。`,
    `战队榜首为 ${topTeam}，可优先关注其近期赛程、胜率和阵容状态。`,
    `${matchLine} 重点选手样本包括 ${topPlayer}，可进入详情页继续查看能力分项。`,
  ]
})
const homeMetricPercent = (value, fallback = 64) => {
  const text = String(value ?? '').trim()
  const percent = text.match(/-?\d+(\.\d+)?\s*%/)
  if (percent) return clamp(Number.parseFloat(percent[0]), 0, 100)
  const num = Number.parseFloat(text.replace(/[^0-9.+-]/g, ''))
  if (!Number.isFinite(num)) return fallback
  if (num <= 2) return clamp(num * 50, 0, 100)
  if (num <= 10) return clamp(num * 10, 0, 100)
  return clamp(num, 0, 100)
}
const triggerHomeAiBriefing = async () => {
  const match = homeHeroMatch.value
  const topTeam = homeTopRanking.value[0]?.name || '暂无队伍'
  const topPlayer = homeTopPlayers.value[0]?.name || '暂无选手'
  let question = `请基于当前${activeGameName.value}首页数据，生成一段适合网站首页展示的赛事情报简报。请覆盖热门战队、重点选手和近期赛程。当前榜首队伍：${topTeam}；重点选手：${topPlayer}。`
  if (match) {
    question += ` 焦点比赛：${match.teamA || '-'} vs ${match.teamB || '-'}，赛事：${match.tournament || '-'}，时间：${match.date || match.matchTime || '-'}。`
  }
  await nextTick()
  aiChatWidgetRef.value?.autoAnalyze(question, aiContextData.value)
}

const openPlayerDetail = (row) => {
  if (!row?.playerId) return
  navigateTo('player-detail', { playerId: row.playerId })
}

const playerPrimaryMetricLabel = computed(() => (isLolGame.value ? 'KDA' : 'Rating'))
const playerSecondaryMetricLabel = computed(() => (isLolGame.value ? '场次' : 'Impact'))
const playerPrimaryMetricValue = (row) =>
  row?.primaryMetricValue || row?.kda || row?.rating || '-'
const playerSecondaryMetricValue = (row) =>
  row?.secondaryMetricValue || row?.gamesPlayed || row?.impact || '-'
const playerScoreValue = (row) => {
  if (!row) return '-'
  if (isLolGame.value) {
    const kda = parseFloat(row.rating || row.kda || 0)
    if (!kda || kda <= 0) return '-'
    return Math.min(99, Math.max(1, Math.round(kda * 15)))
  }
  if (isValorantGame.value) {
    const kd = parseFloat(row.rating || row.kd || 0)
    if (!kd || kd <= 0) return '-'
    return Math.min(99, Math.max(1, Math.round((kd - 0.5) * 100)))
  }
  const rating = parseFloat(row.rating || 0)
  if (!rating || rating <= 0) return '-'
  return Math.min(99, Math.max(1, Math.round((rating - 0.6) * 140)))
}

const playerSearchMetricText = (row) =>
  `${playerPrimaryMetricLabel.value} ${playerPrimaryMetricValue(row)}`

const teamRankHeader = computed(() => {
  if (isLolGame.value || isValorantGame.value) return '排名'
  return teamRankMode.value === 'valve' ? 'Valve排名' : 'HLTV排名'
})
const teamMetricHeaders = computed(() =>
  isLolGame.value
    ? ['比赛数', '胜场', '场均击杀', '场均死亡', '场均助攻']
    : isValorantGame.value
      ? ['胜场', '负场', '比赛数', '胜率', '近期状态']
    : ['地图数', 'K/D', 'Rating', '地图胜率', '场均击杀', '场均死亡'],
)
const teamMetricValue = (row, key) => {
  if (isLolGame.value) {
    const values = [
      row?.matchesPlayed ?? '-',
      row?.wins ?? '-',
      row?.avgKill ?? '-',
      row?.avgDeath ?? '-',
      row?.avgAssist ?? '-',
    ]
    return values[key] ?? '-'
  }
  if (isValorantGame.value) {
    const values = [
      row?.wins ?? '-',
      row?.losses ?? '-',
      row?.matchesPlayed ?? '-',
      row?.winRate ?? '-',
      row?.status ?? '-',
    ]
    return values[key] ?? '-'
  }
  const values = [row?.mapNum, row?.kd, row?.rating, row?.mapWinRate, row?.avgKill, row?.avgDeath]
  return values[key] ?? '-'
}

const openTeamDetail = (row) => {
  const teamKey = String(row?.teamId || row?.team_id || row?.teamKey || row?.name || '').trim()
  if (!teamKey) return
  navigateTo('team-detail', { teamKey })
}

const openTournamentDetail = (row) => {
  const tournamentKey = tournamentRouteKey(row)
  if (!tournamentKey) return
  navigateTo('tournament-detail', { tournamentKey })
}

const openMatchDetail = (row) => {
  const matchId = String(row?.matchId || row?.match_id || '').trim()
  if (!matchId) return
  navigateTo('match-detail', { matchId })
}

const tournamentMatchRows = computed(() => {
  const key = String(selectedTournamentKey.value || '').trim()
  if (!key) return []

  return [...(activeDataset.value?.matches || [])]
    .filter((row) => isSameTournamentName(row?.tournament || row?.event_name || row?.name, key))
    .sort((a, b) => {
      const ta = resolveMatchTimeMs(a)
      const tb = resolveMatchTimeMs(b)
      const aValid = Number.isFinite(ta)
      const bValid = Number.isFinite(tb)
      if (aValid && bValid) return ta - tb
      if (aValid) return -1
      if (bValid) return 1
      return resolveMatchDateText(a).localeCompare(resolveMatchDateText(b))
    })
})

const selectedTournament = computed(() => {
  const key = String(selectedTournamentKey.value || '').trim()
  if (!key) return null

  const tournament = (activeDataset.value?.tournaments || []).find((row) => isSameTournamentName(tournamentNameFromRow(row), key))
  if (tournament) return tournament

  const firstMatch = tournamentMatchRows.value[0]
  if (!firstMatch) return null
  return {
    name: firstMatch.tournament || key,
    tier: resolveRowTierText(firstMatch) || '-',
    region: firstMatch.region || '-',
    start: resolveMatchDateText(firstMatch),
    status: resolveScheduleStatusText(firstMatch),
  }
})

const teamRowByName = computed(() => {
  const map = new Map()
  for (const row of activeDataset.value?.teams || []) {
    const key = normalizeTeamText(row?.name || row?.teamName || row?.team_name)
    if (key && !map.has(key)) map.set(key, row)
  }
  return map
})

const tournamentTeamRows = computed(() => {
  const map = new Map()

  const upsertTeam = (row, side) => {
    const name = String((side === 'A' ? row?.teamA : row?.teamB) || '').trim()
    if (isTbdTeamName(name)) return
    const key = normalizeTeamText(name)
    if (!key) return

    const matchedTeam = teamRowByName.value.get(key) || {}
    const logo = resolveMatchTeamLogo(row, side) || resolveTeamLogo(matchedTeam) || teamLogoByName.value[name] || ''
    const teamKey = String(
      matchedTeam.teamId ||
        matchedTeam.team_id ||
        (side === 'A' ? row?.teamAId || row?.team_a_id : row?.teamBId || row?.team_b_id) ||
        matchedTeam.name ||
        name,
    ).trim()
    const current = map.get(key) || {
      name,
      logo,
      teamKey,
      region: matchedTeam.region || row?.region || '-',
      matches: 0,
      wins: 0,
      losses: 0,
    }

    current.matches += 1
    if (!current.logo && logo) current.logo = logo
    if ((!current.region || current.region === '-') && matchedTeam.region) current.region = matchedTeam.region
    const winnerSide = resolveWinnerSide(row)
    if (winnerSide === side) current.wins += 1
    if (winnerSide && winnerSide !== side) current.losses += 1
    map.set(key, current)
  }

  for (const row of tournamentMatchRows.value) {
    upsertTeam(row, 'A')
    upsertTeam(row, 'B')
  }

  return [...map.values()].sort((a, b) => b.matches - a.matches || b.wins - a.wins || a.name.localeCompare(b.name))
})

const tournamentSummaryCards = computed(() => {
  const matches = tournamentMatchRows.value
  const completed = matches.filter((row) => isFinishedMatch(row)).length
  const live = matches.filter((row) => resolveScheduleStatusText(row) === '进行中').length
  return [
    { key: 'teams', label: '参赛战队', value: String(tournamentTeamRows.value.length) },
    { key: 'matches', label: '比赛总数', value: String(matches.length) },
    { key: 'completed', label: '已完赛', value: String(completed) },
    { key: 'pending', label: live ? '进行中 / 未完赛' : '未完赛', value: String(matches.length - completed) },
  ]
})

const playerCardTeamKey = computed(() => {
  const basic = playerDetail.value?.basic || {}
  const directKey = String(
    basic.teamId || basic.team_id || basic.teamKey || basic.team_key || '',
  ).trim()
  if (directKey) return directKey

  const teamName = String(basic.teamName || basic.team_name || '').trim()
  if (!teamName) return ''

  const normalized = teamName.toLowerCase()
  const matched = (activeDataset.value?.teams || []).find(
    (row) => String(row?.name || '').trim().toLowerCase() === normalized,
  )
  return String(matched?.teamId || matched?.team_id || matched?.name || teamName).trim()
})

const openPlayerCardTeamDetail = () => {
  const teamKey = String(playerCardTeamKey.value || '').trim()
  if (!teamKey) return
  navigateTo('team-detail', { teamKey })
}

const backendDatasetGameIds = new Set(['cs2', 'lol', 'valorant'])
const hasDatasetContent = (dataset) => {
  if (!dataset || typeof dataset !== 'object') return false
  return (
    (Array.isArray(dataset.leaderboard) && dataset.leaderboard.length > 0) ||
    (Array.isArray(dataset.matches) && dataset.matches.length > 0) ||
    (Array.isArray(dataset.tournaments) && dataset.tournaments.length > 0) ||
    (Array.isArray(dataset.teams) && dataset.teams.length > 0) ||
    (Array.isArray(dataset.players) && dataset.players.length > 0)
  )
}

const loadBackendDatasetForGame = async (gameId = selectedGameId.value, options = {}) => {
  if (!backendDatasetGameIds.has(gameId)) return
  const force = Boolean(options?.force)
  if (!force && backendDatasetRequesting.has(gameId)) return
  backendDatasetRequesting.add(gameId)
  try {
    const dataset = await fetchBackendDataset(gameId)
    if (dataset) {
      const prev = datasetByGame.value?.[gameId]
      const nextHasData = hasDatasetContent(dataset)
      const prevHasData = hasDatasetContent(prev)
      if (!nextHasData && prevHasData) {
        console.warn(`[${gameId}-backend-sync-skipped-empty] keep previous non-empty dataset`)
        return
      }
      datasetByGame.value = { ...datasetByGame.value, [gameId]: dataset }
    }
  } catch (error) {
    console.error(`[${gameId}-backend-sync-failed]`, error)
  } finally {
    backendDatasetRequesting.delete(gameId)
  }
}

const stopBackendDatasetRefresh = () => {
  if (backendDatasetRefreshTimer) {
    clearInterval(backendDatasetRefreshTimer)
    backendDatasetRefreshTimer = null
  }
}

const startBackendDatasetRefresh = () => {
  stopBackendDatasetRefresh()
  backendDatasetRefreshTimer = setInterval(() => {
    loadBackendDatasetForGame('cs2')
    loadBackendDatasetForGame('lol')
    loadBackendDatasetForGame('valorant')
  }, BACKEND_DATASET_REFRESH_INTERVAL_MS)
}

const ensureBackendDatasetReady = () => {
  if (!backendDatasetGameIds.has(selectedGameId.value)) return
  const page = currentPage.value
  if (!['home', 'tournaments', 'tournament-detail', 'teams', 'players', 'schedule'].includes(page)) return
  const dataset = datasetByGame.value?.[selectedGameId.value] || {}
  const hasAnyData =
    (Array.isArray(dataset?.leaderboard) && dataset.leaderboard.length > 0) ||
    (Array.isArray(dataset?.matches) && dataset.matches.length > 0) ||
    (Array.isArray(dataset?.tournaments) && dataset.tournaments.length > 0) ||
    (Array.isArray(dataset?.teams) && dataset.teams.length > 0) ||
    (Array.isArray(dataset?.players) && dataset.players.length > 0)
  if (!hasAnyData) {
    loadBackendDatasetForGame(selectedGameId.value, { force: true })
  }
}

const loadBackendScheduleRows = async (options = {}) => {
  const append = Boolean(options?.append)
  if (!backendDatasetGameIds.has(selectedGameId.value) || currentPage.value !== 'schedule') {
    scheduleRowsRequestSeq += 1
    scheduleRowsState.value = 'idle'
    scheduleRowsError.value = ''
    scheduleRowsHasMore.value = false
    scheduleRowsLoadingMore.value = false
    scheduleRowsFromDb.value = []
    return
  }

  if (append) {
    if (
      scheduleRowsState.value === 'loading' ||
      scheduleRowsLoadingMore.value ||
      !scheduleRowsHasMore.value
    ) {
      return
    }
    scheduleRowsLoadingMore.value = true
  } else {
    scheduleRowsState.value = 'loading'
    scheduleRowsError.value = ''
    scheduleRowsHasMore.value = false
    scheduleRowsLoadingMore.value = false
  }

  const currentRows = Array.isArray(scheduleRowsFromDb.value) ? scheduleRowsFromDb.value : []
  const requestOffset = append ? currentRows.length : 0
  const requestSeq = ++scheduleRowsRequestSeq
  const requestGameId = selectedGameId.value
  const requestView = scheduleViewMode.value
  const requestDate = normalizeDateFilter(scheduleDateFilter.value)
  const requestTier = scheduleTierFilter.value
  try {
    const payload = await fetchBackendScheduleMatches(requestGameId, {
      view: requestView,
      date: requestDate,
      tier: requestTier,
      limit: SCHEDULE_PAGE_SIZE,
      offset: requestOffset,
    })
    if (
      requestSeq !== scheduleRowsRequestSeq ||
      requestGameId !== selectedGameId.value ||
      requestView !== scheduleViewMode.value ||
      requestDate !== normalizeDateFilter(scheduleDateFilter.value) ||
      requestTier !== scheduleTierFilter.value ||
      currentPage.value !== 'schedule'
    ) {
      return
    }
    const incomingRows = Array.isArray(payload?.matches) ? payload.matches : []
    if (append) {
      const mergedRows = [...currentRows]
      const seenIds = new Set(
        mergedRows.map((row) => String(row?.matchId || row?.match_id || '').trim()).filter(Boolean),
      )
      for (const row of incomingRows) {
        const mid = String(row?.matchId || row?.match_id || '').trim()
        if (!mid || !seenIds.has(mid)) {
          mergedRows.push(row)
        }
        if (mid) seenIds.add(mid)
      }
      scheduleRowsFromDb.value = mergedRows
    } else {
      scheduleRowsFromDb.value = incomingRows
    }
    scheduleRowsHasMore.value = incomingRows.length >= SCHEDULE_PAGE_SIZE
    scheduleRowsState.value = 'success'
  } catch (error) {
    if (append) {
      scheduleRowsError.value = String(error?.message || error)
      console.error(`[${selectedGameId.value}-schedule-load-more-failed]`, error)
    } else {
      scheduleRowsState.value = 'error'
      scheduleRowsError.value = String(error?.message || error)
      scheduleRowsFromDb.value = []
      scheduleRowsHasMore.value = false
      console.error(`[${selectedGameId.value}-schedule-filter-failed]`, error)
    }
  } finally {
    if (append) {
      scheduleRowsLoadingMore.value = false
    }
  }
}

const hasRealTeamName = (value) => {
  const text = String(value || '').trim()
  if (!text) return false
  const upper = text.toUpperCase()
  return text !== '-' && upper !== 'TBD' && upper !== 'UNKNOWN'
}

const scheduleMatchMergeKey = (row) => {
  const matchId = String(row?.matchId || row?.match_id || '').trim()
  if (matchId) return `id:${matchId}`
  return [
    String(row?.matchTime || '').trim(),
    String(row?.tournament || '').trim(),
    String(row?.teamA || '').trim(),
    String(row?.teamB || '').trim(),
  ].join('|')
}

const mergeSingleMatchRow = (oldRow, liveRow) => {
  const merged = { ...(oldRow || {}), ...(liveRow || {}) }

  if (!hasRealTeamName(liveRow?.teamA) && hasRealTeamName(oldRow?.teamA)) {
    merged.teamA = oldRow.teamA
  }
  if (!hasRealTeamName(liveRow?.teamB) && hasRealTeamName(oldRow?.teamB)) {
    merged.teamB = oldRow.teamB
  }

  if (!String(liveRow?.teamALogo || '').trim() && String(oldRow?.teamALogo || '').trim()) {
    merged.teamALogo = oldRow.teamALogo
  }
  if (!String(liveRow?.teamBLogo || '').trim() && String(oldRow?.teamBLogo || '').trim()) {
    merged.teamBLogo = oldRow.teamBLogo
  }

  const liveScore = String(liveRow?.score || '').trim()
  if ((!liveScore || liveScore === '-' || liveScore === '-:-') && String(oldRow?.score || '').trim()) {
    merged.score = oldRow.score
  }

  const liveWinner = String(liveRow?.winner || '').trim()
  if ((!liveWinner || liveWinner === '-') && String(oldRow?.winner || '').trim()) {
    merged.winner = oldRow.winner
  }

  return merged
}

const isMeaningfulLiveRow = (row) => {
  const matchId = String(row?.matchId || row?.match_id || '').trim()
  if (!matchId) return false
  if (hasRealTeamName(row?.teamA) || hasRealTeamName(row?.teamB)) return true
  const score = String(row?.score || '').trim()
  if (score && score !== '-' && score !== '-:-') return true
  const statusCode = toIntOrNull(row?.statusCode)
  return statusCode === 1 || statusCode === 2
}

const applyLiveMatchesUpdate = (payload) => {
  const rows = payload?.matches
  if (!Array.isArray(rows)) return

  const cs2Current = datasetByGame.value?.cs2 || fallbackDataset
  const baseRows = Array.isArray(cs2Current.matches) ? [...cs2Current.matches] : []
  const indexMap = new Map()
  baseRows.forEach((row, idx) => {
    const key = scheduleMatchMergeKey(row)
    if (key) indexMap.set(key, idx)
  })

  for (const liveRow of rows) {
    if (!isMeaningfulLiveRow(liveRow)) continue
    const key = scheduleMatchMergeKey(liveRow)
    if (!key) continue
    if (indexMap.has(key)) {
      const idx = indexMap.get(key)
      baseRows[idx] = mergeSingleMatchRow(baseRows[idx], liveRow)
    } else {
      baseRows.push(liveRow)
      indexMap.set(key, baseRows.length - 1)
    }
  }

  datasetByGame.value = {
    ...datasetByGame.value,
    cs2: {
      ...cs2Current,
      matches: baseRows,
      updatedAt: payload?.updatedAt || cs2Current.updatedAt || '',
    },
  }
}

const shouldPollScheduleLive = computed(
  () => selectedGameId.value === 'cs2' && currentPage.value === 'schedule',
)

const pollScheduleLiveOnce = async () => {
  if (!shouldPollScheduleLive.value || scheduleLivePollInFlight) return
  scheduleLivePollInFlight = true
  try {
    const payload = await fetchBackendLiveMatches('cs2')
    if (payload) applyLiveMatchesUpdate(payload)
  } catch (error) {
    console.error('[cs2-live-poll-failed]', error)
  } finally {
    scheduleLivePollInFlight = false
  }
}

const stopScheduleLivePolling = () => {
  if (scheduleLivePollTimer) {
    clearInterval(scheduleLivePollTimer)
    scheduleLivePollTimer = null
  }
}

const startScheduleLivePolling = () => {
  stopScheduleLivePolling()
  if (!shouldPollScheduleLive.value) return
  pollScheduleLiveOnce()
  scheduleLivePollTimer = setInterval(() => {
    pollScheduleLiveOnce()
  }, SCHEDULE_LIVE_POLL_INTERVAL_MS)
}

const loadBackendPlayerDetail = async () => {
  if (!backendDatasetGameIds.has(selectedGameId.value) || !selectedPlayerId.value) {
    playerDetail.value = null
    playerDetailState.value = 'idle'
    playerDetailError.value = ''
    return
  }

  playerDetailState.value = 'loading'
  playerDetailError.value = ''
  const requestGameId = selectedGameId.value
  const requestPlayerId = selectedPlayerId.value
  try {
    const detail = await fetchBackendPlayerDetail(requestGameId, requestPlayerId)
    if (selectedGameId.value !== requestGameId || selectedPlayerId.value !== requestPlayerId) return
    playerDetail.value = detail
    playerDetailState.value = 'success'
  } catch (error) {
    if (selectedGameId.value !== requestGameId || selectedPlayerId.value !== requestPlayerId) return
    playerDetailState.value = 'error'
    playerDetailError.value = String(error?.message || error)
    playerDetail.value = null
    console.error(`[${selectedGameId.value}-player-detail-sync-failed]`, error)
  }
}

const loadBackendTeamDetail = async () => {
  if (!backendDatasetGameIds.has(selectedGameId.value) || !selectedTeamKey.value) {
    teamDetail.value = null
    teamDetailState.value = 'idle'
    teamDetailError.value = ''
    teamDetailTab.value = 'data'
    return
  }

  teamDetailState.value = 'loading'
  teamDetailError.value = ''
  try {
    teamDetail.value = await fetchBackendTeamDetail(selectedGameId.value, selectedTeamKey.value)
    teamDetailState.value = 'success'
    teamDetailTab.value = 'data'
  } catch (error) {
    teamDetailState.value = 'error'
    teamDetailError.value = String(error?.message || error)
    teamDetail.value = null
    console.error(`[${selectedGameId.value}-team-detail-sync-failed]`, error)
  }
}

const loadBackendMatchDetail = async () => {
  if (!backendDatasetGameIds.has(selectedGameId.value) || !selectedMatchId.value) {
    matchDetail.value = null
    matchDetailState.value = 'idle'
    matchDetailError.value = ''
    return
  }

  matchDetailState.value = 'loading'
  matchDetailError.value = ''
  try {
    matchDetail.value = await fetchBackendMatchDetail(selectedGameId.value, selectedMatchId.value)
    matchDetailState.value = 'success'
  } catch (error) {
    matchDetailState.value = 'error'
    matchDetailError.value = String(error?.message || error)
    matchDetail.value = null
    console.error(`[${selectedGameId.value}-match-detail-sync-failed]`, error)
  }
}

const exportCurrent = () => {
  const payload = createExportPayload(selectedGameId.value, activeDataset.value)
  if (!payload) return
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json;charset=utf-8' })
  const objectUrl = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = objectUrl
  link.download = `game-league-${payload.gameId}-${new Date().toISOString().slice(0, 10)}.json`
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  URL.revokeObjectURL(objectUrl)
}

watch([selectedGameId, currentPage, selectedPlayerId, selectedTeamKey, selectedMatchId, selectedTournamentKey], () => {
  if (currentPage.value === 'player-detail') {
    loadBackendPlayerDetail()
    return
  }
  if (currentPage.value === 'team-detail') {
    loadBackendTeamDetail()
    return
  }
  if (currentPage.value === 'match-detail') {
    loadBackendMatchDetail()
    return
  }
  if (teamDetail.value || teamDetailState.value !== 'idle') {
    teamDetail.value = null
    teamDetailState.value = 'idle'
    teamDetailError.value = ''
  }
  if (playerDetail.value || playerDetailState.value !== 'idle') {
    playerDetail.value = null
    playerDetailState.value = 'idle'
    playerDetailError.value = ''
  }
  if (matchDetail.value || matchDetailState.value !== 'idle') {
    matchDetail.value = null
    matchDetailState.value = 'idle'
    matchDetailError.value = ''
  }
})


watch(selectedGameId, () => {
  searchKeyword.value = ''
  lolRegionFilter.value = 'all'
  scheduleDateFilter.value = ''
  scheduleViewMode.value = 'fixture'
  scheduleRowsFromDb.value = []
  scheduleRowsState.value = 'idle'
  scheduleRowsError.value = ''
  scheduleRowsHasMore.value = false
  scheduleRowsLoadingMore.value = false
  resetTournamentVisibleRows()
  resetPlayerVisibleRows()
  if (currentPage.value === 'player-detail' || currentPage.value === 'team-detail' || currentPage.value === 'match-detail' || currentPage.value === 'tournament-detail') {
    currentPage.value = 'home'
    selectedPlayerId.value = ''
    selectedTeamKey.value = ''
    selectedMatchId.value = ''
    selectedTournamentKey.value = ''
  }
  navigateTo(currentPage.value, {
    playerId: selectedPlayerId.value,
    teamKey: selectedTeamKey.value,
    matchId: selectedMatchId.value,
    tournamentKey: selectedTournamentKey.value,
  })
  loadBackendDatasetForGame(selectedGameId.value)
})

watch(scheduleDateFilter, (nextValue) => {
  const normalized = normalizeDateFilter(nextValue)
  if (normalized !== nextValue) {
    scheduleDateFilter.value = normalized
  }
})

watch(
  () => filteredRows.value.tournaments.length,
  () => {
    resetTournamentVisibleRows()
  },
)

watch([selectedGameId, currentPage, lolRegionFilter], () => {
  resetPlayerVisibleRows()
})

watch([selectedGameId, currentPage], () => {
  ensureBackendDatasetReady()
  startScheduleLivePolling()
})

watch(currentPage, (nextPage) => {
  if (nextPage === 'tournaments') {
    resetTournamentVisibleRows()
  }
})

const isMatchFinished = (detail) => {
  if (!detail || typeof detail !== 'object') return false
  const winner = String(detail.winner || detail.matchWinner || '').trim()
  if (winner && winner !== '-') return true
  const teamAScore = detail.teamA?.score ?? detail.scoreA ?? detail.teamAScore
  const teamBScore = detail.teamB?.score ?? detail.scoreB ?? detail.teamBScore
  if (teamAScore != null && teamBScore != null && (teamAScore > 0 || teamBScore > 0)) return true
  const statusText = String(detail.status || detail.matchStatus || '').trim().toLowerCase()
  if (statusText === 'completed' || statusText === 'finished' || statusText === '已完赛') return true
  return false
}

watch(
  [() => currentPage.value, () => teamDetailState.value, () => teamDetail.value, teamChartMetrics, teamRecordSummary],
  ([page, state]) => {
    if (page === 'team-detail' && state === 'success') {
      renderTeamCharts()
    } else {
      disposeTeamCharts()
    }
  },
  { deep: true },
)

watch(
  [() => currentPage.value, () => playerDetailState.value, () => playerDetail.value, playerCoreMetrics, playerMapRows, ratingSeries],
  ([page, state]) => {
    if (page === 'player-detail' && state === 'success' && !isLolGame.value) {
      renderPlayerCharts()
    } else {
      disposePlayerCharts()
    }
  },
  { deep: true },
)

watch(
  [() => matchDetailState.value, () => matchDetail.value],
  async ([state, detail]) => {
    if (state !== 'success' || !detail || !isMatchFinished(detail)) return
    const matchId = String(detail.matchId || detail.match_id || selectedMatchId.value || '').trim()
    if (!matchId || matchId === lastAutoAnalyzedMatchId.value) return
    lastAutoAnalyzedMatchId.value = matchId

    const teamA = detail.teamA?.name || detail.teamAName || '队伍A'
    const teamB = detail.teamB?.name || detail.teamBName || '队伍B'
    const scoreA = detail.teamA?.score ?? detail.scoreA ?? detail.teamAScore ?? '-'
    const scoreB = detail.teamB?.score ?? detail.scoreB ?? detail.teamBScore ?? '-'
    const winner = detail.winner || detail.matchWinner || ''
    const tournament = detail.tournament || detail.eventName || detail.league || ''
    const gameLabel = activeGameName.value || ''

    let question = `请分析这场${gameLabel}比赛：${teamA} vs ${teamB}`
    if (tournament) question += `（${tournament}）`
    question += `，比分 ${scoreA}:${scoreB}`
    if (winner) question += `，胜者 ${winner}`
    question += '。请从战术、关键选手表现、地图BP等方面进行分析。'

    await nextTick()
    if (aiChatWidgetRef.value) {
      aiChatWidgetRef.value.autoAnalyze(question, aiContextData.value)
    }
  },
)

watch(
  [selectedGameId, currentPage, scheduleViewMode, scheduleDateFilter, scheduleTierFilter],
  () => {
    loadBackendScheduleRows({ append: false })
  },
)

onMounted(() => {
  if (!window.location.hash) {
    window.location.hash = buildHash(selectedGameId.value, 'home')
  }
  syncFromHash()
  window.addEventListener('hashchange', syncFromHash)
  document.addEventListener('pointerdown', handleDocPointerDown)
  window.addEventListener('keydown', handleWindowKeydown)
  window.addEventListener('resize', resizeAllCharts)
  document.addEventListener('pointerover', handleTablePointerOver)
  window.addEventListener('mouseleave', handleTablePointerLeaveWindow)
  startHomeTeamCarousel()
  startHomePlayerCarousel()
  loadBackendDatasetForGame('cs2')
  loadBackendDatasetForGame('lol')
  loadBackendDatasetForGame('valorant')
  startBackendDatasetRefresh()
  ensureBackendDatasetReady()
  loadBackendScheduleRows()
  startScheduleLivePolling()
})

onBeforeUnmount(() => {
  stopBackendDatasetRefresh()
  stopScheduleLivePolling()
  stopHomeTeamCarousel()
  stopHomePlayerCarousel()
  window.removeEventListener('hashchange', syncFromHash)
  document.removeEventListener('pointerdown', handleDocPointerDown)
  window.removeEventListener('keydown', handleWindowKeydown)
  window.removeEventListener('resize', resizeAllCharts)
  document.removeEventListener('pointerover', handleTablePointerOver)
  window.removeEventListener('mouseleave', handleTablePointerLeaveWindow)
  clearTableHoverState()
  disposeAllCharts()
})
</script>

<template>
  <div class="page-shell" :style="{ '--game-color': activeDataset.color }">
    <header class="site-header" :style="{ '--cover-image': `url('${activeVisual.cover}')` }">
      <div class="top-nav">
        <a class="brand-block brand-link" :href="buildHash(selectedGameId, 'home')" @click.prevent="navigateTo('home')">
          <div class="logo-slot">
            <img class="logo-image" :src="activeVisual.logo" alt="" />
          </div>
          <div class="brand-text">
            <p>Game League</p>
            <h1>{{ activeVisual.title }}</h1>
          </div>
        </a>

        <nav class="menu-block">
          <a
            v-for="item in navItems"
            :key="item.page"
            class="menu-link"
            :class="{ active: currentPage === item.page }"
            :href="buildHash(selectedGameId, item.page)"
            @click.prevent="navigateTo(item.page)"
          >
            <span>{{ item.label }}</span>
          </a>
        </nav>

        <div ref="switchBlockRef" class="switch-block" :class="{ open: isGameMenuOpen }">
          <button
            class="switch-trigger"
            type="button"
            :aria-expanded="isGameMenuOpen"
            @click="toggleGameMenu"
          >
            <span class="switch-current">
              <img class="switch-current-logo" :src="activeVisual.logo" alt="" />
              <span>{{ activeGameName }}</span>
            </span>
            <span class="switch-arrow" :class="{ open: isGameMenuOpen }">▾</span>
          </button>

          <Transition name="switch-dropdown">
            <div v-if="isGameMenuOpen" class="switch-menu" role="menu">
              <button
                v-for="game in gameCatalog"
                :key="game.id"
                class="switch-option"
                :class="{ active: selectedGameId === game.id }"
                type="button"
                role="menuitemradio"
                :aria-checked="selectedGameId === game.id"
                @click="selectGame(game.id)"
              >
                <img class="switch-option-logo" :src="gameVisualMap[game.id]?.logo || activeVisual.logo" alt="" />
                <span>{{ game.name }}</span>
              </button>
            </div>
          </Transition>
        </div>
      </div>
    </header>

    <template v-if="currentPage === 'home'">
      <section class="home-dashboard-hero section-card" :style="{ '--hero-cover': `url('${activeVisual.cover}')` }">
        <div class="home-hero-content">
          <div class="home-hero-kicker">
            <span class="live-dot"></span>
赛事智能看板
          </div>
          <h2>{{ activeGameName }} 数据情报驾驶舱</h2>
          <p class="home-hero-subtitle">
            聚合赛事、赛程、战队、选手与详情数据，把首页变成快速判断赛事情报优先级的指挥中心。
          </p>
          <div class="home-hero-stat-strip">
            <article v-for="item in homeStatsCards" :key="`hero-${item.key}`">
              <span>{{ item.label }}</span>
              <b>{{ item.value }}</b>
            </article>
          </div>
          <ol class="home-hero-insight-list">
            <li v-for="item in homeInsightBullets" :key="`hero-insight-${item}`">{{ item }}</li>
          </ol>
          <div class="home-hero-actions">
            <button class="fill-btn" type="button" @click="navigateTo('schedule')">进入赛程中心</button>
            <button class="outline-btn hero-outline" type="button" @click="triggerHomeAiBriefing">AI 生成今日看点</button>
          </div>
        </div>

        <article class="hero-match-card">
          <div class="hero-match-topline">
            <span>焦点对阵</span>
            <b>{{ homeHeroMatch ? resolveScheduleStatusText(homeHeroMatch) : '待更新' }}</b>
          </div>
          <template v-if="homeHeroMatch">
            <p class="hero-match-tournament">{{ homeHeroMatch.tournament || '-' }}</p>
            <div class="hero-versus-row">
              <div class="hero-versus-team">
                <img v-if="resolveMatchTeamLogo(homeHeroMatch, 'A')" :src="resolveMatchTeamLogo(homeHeroMatch, 'A')" alt="" loading="lazy" @error="markBrokenImage" />
                <b v-else>{{ playerInitial(homeHeroMatch.teamA) }}</b>
                <span>{{ homeHeroMatch.teamA || '-' }}</span>
              </div>
              <div class="hero-score-block">
                <strong>{{ formatScheduleScore(homeHeroMatch) }}</strong>
                <small>{{ resolveMatchKickoffTime(homeHeroMatch) }}</small>
              </div>
              <div class="hero-versus-team">
                <img v-if="resolveMatchTeamLogo(homeHeroMatch, 'B')" :src="resolveMatchTeamLogo(homeHeroMatch, 'B')" alt="" loading="lazy" @error="markBrokenImage" />
                <b v-else>{{ playerInitial(homeHeroMatch.teamB) }}</b>
                <span>{{ homeHeroMatch.teamB || '-' }}</span>
              </div>
            </div>
            <button class="hero-match-link" type="button" @click="homeHeroMatch.matchId || homeHeroMatch.match_id ? openMatchDetail(homeHeroMatch) : navigateTo('schedule')">
              进入比赛情报
            </button>
          </template>
          <div v-else class="empty-state hero-empty">暂无焦点比赛</div>
        </article>
      </section>

      <section class="section-card home-search-card home-search-dashboard-card">
        <div class="section-topline">
          <h2>全站智能检索</h2>
          <span class="section-count">共 {{ homeSearchResults.total }} 条</span>
        </div>
        <div class="home-search-input-row">
          <input
            v-model="searchKeyword"
            type="text"
            placeholder="搜索战队、选手或系列赛（赛事名 / 对阵）"
            @keydown.esc="searchKeyword = ''"
          />
          <button
            v-if="homeSearchText"
            type="button"
            class="home-search-clear"
            @click="searchKeyword = ''"
          >
            清空
          </button>

          <Transition name="home-search-dropdown">
            <div v-if="homeSearchText" class="home-search-dropdown">
              <div v-if="!homeSearchResults.total" class="home-search-empty">
                没有找到相关结果，请尝试更换关键词
              </div>
              <div v-else class="home-search-result-grid">
                <article class="home-search-block">
                  <h3>相关战队</h3>
                  <div v-if="!homeSearchResults.teams.length" class="empty-state">暂无匹配战队</div>
                  <button
                    v-for="row in homeSearchResults.teams"
                    :key="`team-${row.name}`"
                    type="button"
                    class="home-search-item"
                    @click="openSearchResult('team', row)"
                  >
                    <span class="team-with-logo">
                      <span v-if="resolveTeamLogo(row)" class="team-logo-badge-wrap">
                        <img class="team-logo-badge" :src="resolveTeamLogo(row)" alt="" loading="lazy" @error="markBrokenImage" />
                      </span>
                      <span class="team-name-text">{{ row.name || '-' }}</span>
                    </span>
                    <span>{{ row.region || '-' }} · {{ row.form || row.style || '-' }}</span>
                  </button>
                </article>

                <article class="home-search-block">
                  <h3>相关选手</h3>
                  <div v-if="!homeSearchResults.players.length" class="empty-state">暂无匹配选手</div>
                  <button
                    v-for="row in homeSearchResults.players"
                    :key="`player-${row.playerId || row.name}`"
                    type="button"
                    class="home-search-item"
                    @click="openSearchResult('player', row)"
                  >
                    <span class="player-cell">
                      <img v-if="usableImage(row.avatar)" class="player-avatar" :src="usableImage(row.avatar)" alt="" loading="lazy" referrerpolicy="no-referrer" @error="markBrokenImage" />
                      <b v-else class="player-avatar-fallback">{{ playerInitial(row.name) }}</b>
                      <span class="player-name">{{ row.name || '-' }}</span>
                    </span>
                    <span>{{ row.team || '-' }} · {{ row.role || '-' }} · {{ playerSearchMetricText(row) }}</span>
                  </button>
                </article>

                <article class="home-search-block home-search-block-wide">
                  <h3>相关系列赛</h3>
                  <div v-if="!homeSearchResults.series.length" class="empty-state">暂无匹配系列赛</div>
                  <button
                    v-for="row in homeSearchResults.series"
                    :key="`series-${row.date}-${row.tournament}-${row.teamA}-${row.teamB}`"
                    type="button"
                    class="home-search-item"
                    @click="openSearchResult('series', row)"
                  >
                    <span>{{ row.date || '-' }} · {{ row.tournament || '-' }} · {{ row.stage || '-' }}</span>
                    <span>{{ row.teamA || '-' }} vs {{ row.teamB || '-' }} · {{ row.score || '-' }}</span>
                  </button>
                </article>
              </div>
            </div>
          </Transition>
        </div>
      </section>

      <section class="section-card home-dashboard-section">
        <div class="section-topline dashboard-topline">
          <div>
            <p class="section-eyebrow">首页概览</p>
            <h2>首页概览</h2>
          </div>
          <span class="section-count">{{ homeMatchCount }} 场比赛样本</span>
        </div>
        <div v-if="!homeHasData" class="empty-state">暂无首页数据，请先选择有数据的游戏</div>
        <template v-if="homeHasData">
          <div class="dashboard-kpi-grid">
            <article v-for="item in homeStatsCards" :key="item.key" class="dashboard-kpi-card">
              <p>{{ item.label }}</p>
              <strong>{{ item.value }}</strong>
              <span>{{ item.hint }}</span>
              <i :style="{ width: `${homeMetricPercent(item.value)}%` }"></i>
            </article>
          </div>

          <div class="dashboard-main-grid">
            <article
              class="preview-panel dashboard-panel ranking-panel home-interactive-panel"
              @mousemove="updateHomePanelGlow"
              @mouseleave="clearHomePanelGlow($event); resumeHomeCarousel('teams')"
              @mouseenter="pauseHomeCarousel('teams')"
              @click="openHomePanel('teams')"
            >
              <div class="panel-title-row">
                <h3>战队情报榜</h3>
                <button class="link-btn" type="button" @click.stop="navigateTo('teams')">完整榜单</button>
              </div>
              <Transition name="home-team-slide" mode="out-in">
                <div :key="`team-page-${homeTeamCarouselPage}`" class="spotlight-team-list home-team-carousel-page">
                  <button
                    v-for="(row, idx) in homeFeaturedTeams"
                    :key="`${row.regionRank || row.rank || idx}-${row.name}`"
                    class="spotlight-team-card"
                    type="button"
                    @click.stop="openTeamDetail(row)"
                  >
                    <span class="spotlight-rank">#{{ row.regionRank || row.rank || homeTeamCarouselPage * 4 + idx + 1 }}</span>
                    <span class="team-with-logo">
                      <span v-if="resolveTeamLogo(row)" class="team-logo-badge-wrap">
                        <img class="team-logo-badge" :src="resolveTeamLogo(row)" alt="" loading="lazy" @error="markBrokenImage" />
                      </span>
                      <span class="team-name-text">{{ row.name || '-' }}</span>
                    </span>
                    <span>{{ row.region || '-' }}</span>
                    <b>{{ row.winRate || row.points || '-' }}</b>
                  </button>
                </div>
              </Transition>
              <small class="home-panel-hint">点击展开 TOP 12</small>
            </article>

            <article
              class="preview-panel dashboard-panel player-spotlight-panel home-interactive-panel"
              @mousemove="updateHomePanelGlow"
              @mouseleave="clearHomePanelGlow($event); resumeHomeCarousel('players')"
              @mouseenter="pauseHomeCarousel('players')"
              @click="openHomePanel('players')"
            >
              <div class="panel-title-row">
                <h3>明星选手</h3>
                <button class="link-btn" type="button" @click.stop="navigateTo('players')">选手库</button>
              </div>
              <Transition name="home-player-slide" mode="out-in">
                <div :key="`player-page-${homePlayerCarouselPage}`" class="home-player-showcase home-player-carousel-page">
                  <button
                    v-for="row in homeFeaturedPlayers"
                    :key="row.playerId || row.name"
                    type="button"
                    class="home-player-card"
                    @click.stop="openPlayerDetail(row)"
                  >
                    <img v-if="usableImage(row.avatar)" :src="usableImage(row.avatar)" alt="" loading="lazy" referrerpolicy="no-referrer" @error="markBrokenImage" />
                    <b v-else>{{ playerInitial(row.name) }}</b>
                    <span>{{ row.name || '-' }}</span>
                    <small>{{ row.team || '-' }} · {{ row.role || '-' }}</small>
                    <strong>{{ playerPrimaryMetricLabel }} {{ playerPrimaryMetricValue(row) }}</strong>
                  </button>
                </div>
              </Transition>
              <small class="home-panel-hint">点击展开 TOP 12</small>
            </article>

            <article class="preview-panel dashboard-panel insight-panel home-interactive-panel" @mousemove="updateHomePanelGlow" @mouseleave="clearHomePanelGlow">
              <div class="panel-title-row">
                <h3>AI 情报摘要</h3>
                <button class="link-btn" type="button" @click="triggerHomeAiBriefing">重新生成</button>
              </div>
              <ol class="home-insight-list">
                <li v-for="item in homeInsightBullets" :key="item">{{ item }}</li>
              </ol>
            </article>
          </div>

          <Transition name="home-expanded-pop">
            <div v-if="expandedHomePanel" class="home-expanded-overlay" @click.self="closeHomePanel">
              <article class="home-expanded-panel" :class="`panel-${expandedHomePanel}`">
                <div class="panel-title-row home-expanded-head">
                  <div>
                    <h3>{{ expandedHomePanel === 'teams' ? '战队情报榜 TOP 12' : '明星选手 TOP 12' }}</h3>
                    <span>{{ expandedHomePanel === 'teams' ? '完整展示当前首页战队排名样本' : '完整展示当前首页明星选手样本' }}</span>
                  </div>
                  <button class="link-btn" type="button" @click="closeHomePanel">收起</button>
                </div>

                <div v-if="expandedHomePanel === 'teams'" class="home-expanded-team-grid">
                  <button
                    v-for="(row, idx) in homeExpandedTeams"
                    :key="`expanded-team-${row.teamId || row.name || idx}`"
                    type="button"
                    class="spotlight-team-card home-expanded-team-card"
                    @click="openTeamDetail(row)"
                  >
                    <span class="spotlight-rank">#{{ row.regionRank || row.rank || idx + 1 }}</span>
                    <span class="team-with-logo">
                      <span v-if="resolveTeamLogo(row)" class="team-logo-badge-wrap">
                        <img class="team-logo-badge" :src="resolveTeamLogo(row)" alt="" loading="lazy" @error="markBrokenImage" />
                      </span>
                      <span class="team-name-text">{{ row.name || '-' }}</span>
                    </span>
                    <span>{{ row.region || '-' }}</span>
                    <b>{{ row.winRate || row.points || '-' }}</b>
                  </button>
                </div>

                <div v-else class="home-expanded-player-grid">
                  <button
                    v-for="(row, idx) in homeExpandedPlayers"
                    :key="`expanded-player-${row.playerId || row.name || idx}`"
                    type="button"
                    class="home-player-card home-expanded-player-card"
                    @click="openPlayerDetail(row)"
                  >
                    <span class="spotlight-rank">#{{ idx + 1 }}</span>
                    <img v-if="usableImage(row.avatar)" :src="usableImage(row.avatar)" alt="" loading="lazy" referrerpolicy="no-referrer" @error="markBrokenImage" />
                    <b v-else>{{ playerInitial(row.name) }}</b>
                    <span>{{ row.name || '-' }}</span>
                    <small>{{ row.team || '-' }} · {{ row.role || '-' }}</small>
                    <strong>{{ playerPrimaryMetricLabel }} {{ playerPrimaryMetricValue(row) }}</strong>
                  </button>
                </div>
              </article>
            </div>
          </Transition>

          <div class="focus-grid dashboard-focus-grid">
            <article class="focus-card match-feed-card home-interactive-panel" @mousemove="updateHomePanelGlow" @mouseleave="clearHomePanelGlow">
              <div class="panel-title-row">
                <h3>近期比赛情报流</h3>
                <button class="link-btn" type="button" @click="navigateTo('schedule')">赛程中心</button>
              </div>
              <div class="home-match-feed">
                <button
                  v-for="row in homeRecentMatches"
                  :key="`${row.date}-${row.tournament}-${row.teamA}-${row.teamB}`"
                  type="button"
                  class="home-match-row"
                  @click="row.matchId || row.match_id ? openMatchDetail(row) : navigateTo('schedule')"
                >
                  <span>{{ resolveMatchDateText(row) }}</span>
                  <b class="home-match-teams">
                    <span class="home-match-team-side">
                      <img v-if="resolveMatchTeamLogo(row, 'A')" :src="resolveMatchTeamLogo(row, 'A')" alt="" loading="lazy" @error="markBrokenImage" />
                      <i v-else>{{ playerInitial(row.teamA) }}</i>
                      <strong>{{ row.teamA || '-' }}</strong>
                    </span>
                    <em>vs</em>
                    <span class="home-match-team-side">
                      <img v-if="resolveMatchTeamLogo(row, 'B')" :src="resolveMatchTeamLogo(row, 'B')" alt="" loading="lazy" @error="markBrokenImage" />
                      <i v-else>{{ playerInitial(row.teamB) }}</i>
                      <strong>{{ row.teamB || '-' }}</strong>
                    </span>
                  </b>
                  <small>
                    <span v-if="resolveScheduleStatusText(row) === '进行中'" class="home-live-pill">进行中</span>
                    {{ row.tournament || '-' }} · {{ formatScheduleScore(row) }}
                  </small>
                </button>
                <div v-if="!homeRecentMatches.length" class="empty-state">暂无数据</div>
              </div>
            </article>
            <article class="focus-card tournament-feed-card home-interactive-panel" @mousemove="updateHomePanelGlow" @mouseleave="clearHomePanelGlow">
              <div class="panel-title-row">
                <h3>热门赛事雷达</h3>
                <button class="link-btn" type="button" @click="navigateTo('tournaments')">赛事中心</button>
              </div>
              <div class="tournament-chip-grid">
                <button
                  v-for="row in homeFeaturedTournaments"
                  :key="`${row.name}-${row.start}`"
                  type="button"
                  class="tournament-chip"
                  @click="openTournamentDetail(row)"
                >
                  <b>{{ row.name || '-' }}</b>
                  <span>{{ row.tier || row.grade || '-' }} · {{ row.region || '-' }}</span>
                  <small>{{ row.start || '-' }} → {{ row.end || row.status || '-' }}</small>
                </button>
                <div v-if="!homeFeaturedTournaments.length" class="empty-state">暂无数据</div>
              </div>
            </article>
          </div>
        </template>
      </section>
    </template>

    <template v-else>
      <SchedulePage
        v-if="currentPage === 'schedule'"
        :view-mode="scheduleViewMode"
        :date-filter="scheduleDateFilter"
        :date-min="scheduleDateMin"
        :date-max="scheduleDateMax"
        :tier-filter="scheduleTierFilter"
        :tier-options="scheduleTierFilterOptions"
        :rows="filteredScheduleRows"
        :has-more="scheduleRowsHasMore"
        :loading-more="scheduleRowsLoadingMore"
        :loading="scheduleRowsState === 'loading'"
        :error="scheduleRowsError"
        :resolve-match-date-text="resolveMatchDateText"
        :resolve-match-team-logo="resolveMatchTeamLogo"
        :resolve-schedule-score-part="resolveScheduleScorePart"
        :is-result-winner="isResultWinner"
        :is-result-loser="isResultLoser"
        :resolve-match-kickoff-time="resolveMatchKickoffTime"
        :resolve-schedule-status-text="resolveScheduleStatusText"
        :resolve-row-tier-text="resolveRowTierText"
        :resolve-schedule-prediction="resolveSchedulePrediction"
        :is-tbd-team-name="isTbdTeamName"
        :image-error-handler="markBrokenImage"
        @update:view-mode="scheduleViewMode = $event"
        @update:date-filter="scheduleDateFilter = $event"
        @update:tier-filter="scheduleTierFilter = $event"
        @open-match="openMatchDetail"
        @load-more="loadBackendScheduleRows({ append: true })"
      />

      <MatchDetailPage
        v-if="currentPage === 'match-detail'"
        :state="matchDetailState"
        :error="matchDetailError"
        :detail="matchDetail"
        :game-id="selectedGameId"
        :ensure-cropped-logo="ensureCroppedLogo"
        :resolve-map-image="resolveMapImage"
        :format-map-name="formatMapName"
        :image-error-handler="markBrokenImage"
        @back="navigateTo('schedule')"
      />

      <TournamentDetailPage
        v-if="currentPage === 'tournament-detail'"
        :tournament="selectedTournament"
        :teams="tournamentTeamRows"
        :matches="tournamentMatchRows"
        :summary-cards="tournamentSummaryCards"
        :resolve-match-date-text="resolveMatchDateText"
        :resolve-match-team-logo="resolveMatchTeamLogo"
        :resolve-schedule-score-part="resolveScheduleScorePart"
        :is-result-winner="isMatchSideWinner"
        :is-result-loser="isMatchSideLoser"
        :resolve-match-kickoff-time="resolveMatchKickoffTime"
        :resolve-schedule-status-text="resolveScheduleStatusText"
        :image-error-handler="markBrokenImage"
        @back="navigateTo('tournaments')"
        @open-match="openMatchDetail"
        @open-team="openTeamDetail"
      />

      <section v-if="currentPage === 'tournaments'" class="section-card">
        <div class="section-topline">
          <h2>赛事</h2>
          <div class="section-topline-right">
            <div v-if="isRegionRankGame" class="team-rank-toggle region-filter-toggle">
              <button
                v-for="item in lolRegionOptions"
                :key="`tournament-region-${item.value}`"
                type="button"
                class="team-rank-btn"
                :class="{ active: lolRegionFilter === item.value }"
                @click="lolRegionFilter = item.value"
              >
                {{ item.label }}
              </button>
            </div>
            <span class="section-count">{{ isRegionRankGame ? lolTournamentRows.length : visibleTournamentRows.length }} / {{ filteredRows.tournaments.length }} 条</span>
          </div>
        </div>

        <div v-if="isRegionRankGame" class="region-page-stack">
          <div v-if="!lolTournamentGroups.length" class="empty-state">暂无匹配数据</div>
          <section v-for="group in lolTournamentGroups" :key="`tournament-group-${group.region}`" class="region-data-section">
            <div class="region-data-title">
              <h3>{{ group.region }}</h3>
              <span>{{ group.rows.length }} 项赛事</span>
            </div>
            <div class="table-wrap">
              <div class="table-head tournament-grid"><span>赛事</span><span>级别</span><span>赛区</span><span>开始日期</span><span>结束日期</span><span>状态</span></div>
              <button v-for="row in group.rows" :key="row.name + row.start" type="button" class="table-row tournament-grid tournament-clickable-row" @click="openTournamentDetail(row)">
                <span>
                  {{ row.name }}
                  <small v-if="row.bilibiliLive?.supported" class="tournament-inline-live-flag" :class="{ live: row.bilibiliLive?.status === 'live' }">
                    <i v-if="row.bilibiliLive?.status === 'live'" class="bilibili-live-dot"></i>
                    {{ row.bilibiliLive?.status === 'live' ? '正在直播' : '有直播' }}
                  </small>
                </span><span>{{ row.tier }}</span><span>{{ row.region }}</span><span>{{ row.start || '-' }}</span><span>{{ row.end || '-' }}</span><span>{{ row.status }}</span>
              </button>
            </div>
          </section>
        </div>

        <div v-else class="table-wrap tournament-scroll-wrap" @scroll="handleTournamentScroll">
          <div class="table-head tournament-grid tournament-head-sticky"><span>赛事</span><span>级别</span><span>赛区</span><span>开始日期</span><span>结束日期</span><span>状态</span></div>
          <div v-if="!visibleTournamentRows.length" class="empty-state">暂无匹配数据</div>
          <button v-for="row in visibleTournamentRows" :key="row.name + row.start" type="button" class="table-row tournament-grid tournament-clickable-row" @click="openTournamentDetail(row)">
            <span>
              {{ row.name }}
              <small v-if="row.bilibiliLive?.supported" class="tournament-inline-live-flag" :class="{ live: row.bilibiliLive?.status === 'live' }">
                <i v-if="row.bilibiliLive?.status === 'live'" class="bilibili-live-dot"></i>
                {{ row.bilibiliLive?.status === 'live' ? '正在直播' : '有直播' }}
              </small>
            </span><span>{{ row.tier }}</span><span>{{ row.region }}</span><span>{{ row.start || '-' }}</span><span>{{ row.end || '-' }}</span><span>{{ row.status }}</span>
          </button>
        </div>
        <div v-if="!isRegionRankGame && hasMoreTournamentRows" class="empty-state tournament-load-tip">向下滚动以继续加载更多赛事（每次 20 条）</div>
      </section>

      <section v-if="currentPage === 'teams'" class="section-card">
        <div class="section-topline">
          <h2>战队</h2>
          <div class="section-topline-right">
            <div v-if="isRegionRankGame" class="team-rank-toggle region-filter-toggle">
              <button
                v-for="item in lolRegionOptions"
                :key="`team-region-${item.value}`"
                type="button"
                class="team-rank-btn"
                :class="{ active: lolRegionFilter === item.value }"
                @click="lolRegionFilter = item.value"
              >
                {{ item.label }}
              </button>
            </div>
            <div v-if="!isLolGame && !isValorantGame" class="team-rank-toggle">
              <button
                type="button"
                class="team-rank-btn"
                :class="{ active: teamRankMode === 'valve' }"
                @click="teamRankMode = 'valve'"
              >
                Valve 排名
              </button>
              <button
                type="button"
                class="team-rank-btn"
                :class="{ active: teamRankMode === 'hltv' }"
                @click="teamRankMode = 'hltv'"
              >
                HLTV 排名
              </button>
            </div>
            <span class="section-count">{{ isRegionRankGame ? lolTeamRows.length : teamRowsWithRanking.length }} 条</span>
          </div>
        </div>
        <div v-if="isRegionRankGame" class="region-page-stack">
          <div v-if="!lolTeamGroups.length" class="empty-state">暂无匹配数据</div>
          <section v-for="group in lolTeamGroups" :key="`team-group-${group.region}`" class="region-data-section">
            <div class="region-data-title">
              <h3>{{ group.region }}</h3>
              <span>{{ group.rows.length }} 支战队</span>
            </div>
            <div class="table-wrap">
              <div class="table-head team-grid">
                <span>赛区排名</span><span>战队</span><span>评分</span><span>胜率</span><span>趋势</span>
                <span v-for="label in teamMetricHeaders" :key="`lol-team-${label}`">{{ label }}</span>
              </div>
              <div v-for="row in group.rows" :key="row.teamId || row.name" class="table-row team-grid">
                <span>{{ row.regionRank || row.rank || '-' }}</span>
                <button class="link-btn team-row-link" type="button" @click="openTeamDetail(row)">
                  <span class="team-with-logo">
                    <span v-if="resolveTeamLogo(row)" class="team-logo-badge-wrap">
                      <img
                        class="team-logo-badge"
                        :src="resolveTeamLogo(row)"
                        alt=""
                        loading="lazy"
                        @error="markBrokenImage"
                      />
                    </span>
                    <span class="team-name-text">{{ row.name }}</span>
                  </span>
                </button>
                <span>{{ row.points ?? '-' }}</span>
                <span>{{ row.winRate ?? '-' }}</span>
                <span>{{ row.trend ?? '-' }}</span>
                <span v-for="(_, idx) in teamMetricHeaders" :key="`lol-team-metric-${idx}`">{{ teamMetricValue(row, idx) }}</span>
              </div>
            </div>
          </section>
        </div>

        <div v-else class="table-wrap">
          <div class="table-head team-grid">
            <span>{{ teamRankHeader }}</span><span>战队</span><span>评分</span><span>胜率</span><span>趋势</span>
            <span v-for="label in teamMetricHeaders" :key="label">{{ label }}</span>
          </div>
          <div v-if="!teamRowsWithRanking.length" class="empty-state">暂无匹配数据</div>
          <div v-for="row in teamRowsWithRanking" :key="row.teamId || row.name" class="table-row team-grid">
            <span>{{ currentTeamRank(row) || '-' }}</span>
            <button class="link-btn team-row-link" type="button" @click="openTeamDetail(row)">
              <span class="team-with-logo">
                <span v-if="resolveTeamLogo(row)" class="team-logo-badge-wrap">
                  <img
                    class="team-logo-badge"
                    :src="resolveTeamLogo(row)"
                    alt=""
                    loading="lazy"
                    @error="markBrokenImage"
                  />
                </span>
                <span class="team-name-text">{{ row.name }}</span>
              </span>
            </button>
            <span>{{ row.points ?? '-' }}</span>
            <span>{{ row.winRate ?? '-' }}</span>
            <span>{{ row.trend ?? '-' }}</span>
            <span v-for="(_, idx) in teamMetricHeaders" :key="`team-metric-${idx}`">{{ teamMetricValue(row, idx) }}</span>
          </div>
        </div>
      </section>

      <section v-if="currentPage === 'players'" class="section-card">
        <div class="section-topline">
          <h2>选手</h2>
          <div class="section-topline-right">
            <div v-if="isRegionRankGame" class="team-rank-toggle region-filter-toggle">
              <button
                v-for="item in lolRegionOptions"
                :key="`player-region-${item.value}`"
                type="button"
                class="team-rank-btn"
                :class="{ active: lolRegionFilter === item.value }"
                @click="lolRegionFilter = item.value"
              >
                {{ item.label }}
              </button>
            </div>
            <span class="section-count">{{ visiblePlayerCount }} / {{ playerTotalCount }} 条</span>
          </div>
        </div>

        <div v-if="isRegionRankGame" class="region-page-stack player-scroll-wrap" @scroll.passive="handlePlayerScroll">
          <div v-if="!lolPlayerGroups.length" class="empty-state">暂无匹配数据</div>
          <section v-for="group in lolPlayerGroups" :key="`player-group-${group.region}`" class="region-data-section">
            <div class="region-data-title">
              <h3>{{ group.region }}</h3>
              <span>{{ group.rows.length }} 名选手</span>
            </div>
            <div class="table-wrap">
              <div class="table-head player-grid"><span>赛区排名</span><span>选手</span><span>所属战队</span><span>位置</span><span>{{ playerPrimaryMetricLabel }}</span><span>{{ playerSecondaryMetricLabel }}</span><span>评分</span></div>
              <div v-for="row in group.rows" :key="row.playerKey || row.playerId" class="table-row player-grid">
                <span>{{ row.displayRank || row.rank || '-' }}</span>
                <span class="player-cell">
                  <img v-if="usableImage(row.avatar)" class="player-avatar" :src="usableImage(row.avatar)" alt="" loading="lazy" referrerpolicy="no-referrer" @error="markBrokenImage" />
                  <b v-else class="player-avatar-fallback">{{ playerInitial(row.name) }}</b>
                  <button class="link-btn player-name" type="button" @click="openPlayerDetail(row)">{{ row.name }}</button>
                </span>
                <span>{{ row.team }}</span><span>{{ row.role }}</span><span>{{ playerPrimaryMetricValue(row) }}</span><span>{{ playerSecondaryMetricValue(row) }}</span><span>{{ playerScoreValue(row) }}</span>
              </div>
            </div>
          </section>
          <button v-if="hasMorePlayerRows" class="outline-btn load-more-btn" type="button" @click="loadMorePlayerRows">加载更多</button>
        </div>

        <div v-else class="table-wrap player-scroll-wrap" @scroll.passive="handlePlayerScroll">
          <div class="table-head player-grid"><span>排名</span><span>选手</span><span>所属战队</span><span>角色</span><span>{{ playerPrimaryMetricLabel }}</span><span>{{ playerSecondaryMetricLabel }}</span><span>评分</span></div>
          <div v-if="!playerRowsWithRank.length" class="empty-state">暂无匹配数据</div>
          <div v-for="row in visiblePlayerRowsWithRank" :key="row.playerKey || row.playerId" class="table-row player-grid">
            <span>{{ row.displayRank }}</span>
            <span class="player-cell">
              <img v-if="usableImage(row.avatar)" class="player-avatar" :src="usableImage(row.avatar)" alt="" loading="lazy" referrerpolicy="no-referrer" @error="markBrokenImage" />
              <b v-else class="player-avatar-fallback">{{ playerInitial(row.name) }}</b>
              <button class="link-btn player-name" type="button" @click="openPlayerDetail(row)">{{ row.name }}</button>
            </span>
            <span>{{ row.team }}</span><span>{{ row.role }}</span><span>{{ playerPrimaryMetricValue(row) }}</span><span>{{ playerSecondaryMetricValue(row) }}</span><span>{{ playerScoreValue(row) }}</span>
          </div>
          <button v-if="hasMorePlayerRows" class="outline-btn load-more-btn" type="button" @click="loadMorePlayerRows">加载更多</button>
        </div>
      </section>

      <section v-if="currentPage === 'team-detail'" class="section-card">
        <div class="section-topline">
          <h2>战队详情</h2>
          <button class="outline-btn" type="button" @click="navigateTo('teams')">返回战队列表</button>
        </div>

        <div v-if="teamDetailState === 'loading'" class="empty-state">正在加载战队详情...</div>
        <div v-else-if="teamDetailState === 'error'" class="empty-state">加载失败：{{ teamDetailError }}</div>
        <div v-else-if="!teamDetail || !teamDetail.basic" class="empty-state">暂无战队详情数据</div>

        <div v-else class="team-detail-stack team-intel-stack">
          <!-- 1. Hero -->
          <article class="team-hero-card team-intel-hero" :style="teamLogoMaskStyle(ensureCroppedLogo(teamDetail.basic.teamLogo, teamDetail.basic.teamName))">
            <div class="team-hero-left team-intel-hero-left">
              <span class="team-hero-logo-wrap team-intel-logo-wrap" v-if="ensureCroppedLogo(teamDetail.basic.teamLogo, teamDetail.basic.teamName)">
                <img
                  class="team-hero-logo"
                  :src="ensureCroppedLogo(teamDetail.basic.teamLogo, teamDetail.basic.teamName)"
                  alt=""
                  loading="lazy"
                  @error="markBrokenImage"
                />
              </span>
              <b v-else class="team-hero-fallback team-intel-logo-fallback">{{ playerInitial(teamDetail.basic.teamName) }}</b>
              <div class="team-hero-text team-intel-title">
                <p class="section-eyebrow">Team Intelligence File</p>
                <h3>{{ teamDetail.basic.teamName || '-' }}</h3>
                <div class="team-identity-chip-row">
                  <span v-for="item in teamIdentityChips" :key="item">{{ item }}</span>
                  <span>{{ activeGameName }}</span>
                  <span>成员 {{ teamDetail.members?.length || 0 }} / 5</span>
                </div>
              </div>
            </div>
            <div class="team-intel-actions">
              <button class="outline-btn" type="button" @click="navigateTo('teams')">返回战队列表</button>
              <button class="fill-btn" type="button" @click="triggerTeamAiAnalysis">AI 分析战队</button>
            </div>
          </article>

          <!-- 2. KPI -->
          <section class="team-kpi-grid">
            <article v-for="item in teamHeroKpis" :key="item.label" class="team-kpi-card" :class="`tone-${item.tone}`">
              <span>{{ item.label }}</span>
              <strong>{{ item.value }}</strong>
              <i :style="{ width: `${teamMetricRatio(item.value)}%` }"></i>
            </article>
          </section>

          <!-- 3. Roster -->
          <article class="detail-card team-roster-wall-card">
            <div class="panel-title-row">
              <h3>首发阵容</h3>
              <span class="section-count">点击进入选手详情</span>
            </div>
            <div class="team-member-grid team-roster-wall">
              <button
                v-for="row in teamMembers"
                :key="row.playerId"
                type="button"
                class="team-member-card team-roster-card"
                :class="{ disabled: row.isPlaceholder || !row.playerId }"
                :disabled="row.isPlaceholder || !row.playerId"
                @click="!row.isPlaceholder && row.playerId && navigateTo('player-detail', { playerId: row.playerId })"
              >
                <img v-if="usableImage(row.avatar)" class="team-member-avatar" :src="usableImage(row.avatar)" alt="" loading="lazy" referrerpolicy="no-referrer" @error="markBrokenImage" />
                <b v-else class="team-member-avatar-fallback">{{ playerInitial(row.name) }}</b>
                <div class="team-member-meta">
                  <p class="team-member-name">{{ row.name || '-' }}</p>
                  <p class="team-member-extra">{{ row.position || row.role || '-' }}</p>
                  <strong>{{ teamMemberMetricValue(row) }}</strong>
                </div>
              </button>
            </div>
          </article>

          <!-- 4. Charts row: radar + bar + ranking -->
          <div class="team-intel-main-grid">
            <article class="detail-card team-chart-card">
              <div class="panel-title-row">
                <h3>综合能力雷达</h3>
                <span class="section-count">ECharts</span>
              </div>
              <div ref="teamRadarChartRef" class="team-echart"></div>
            </article>

            <article class="detail-card team-chart-card">
              <div class="panel-title-row">
                <h3>近期战绩分布</h3>
                <span class="section-count">{{ teamRecordSummary.total }} 场</span>
              </div>
              <div ref="teamRecordChartRef" class="team-echart"></div>
            </article>

            <article class="detail-card team-ranking-brief-card">
              <div class="panel-title-row">
                <h3>排名档案</h3>
                <span class="section-count">{{ teamRankingBrief.length }} 项</span>
              </div>
              <div class="team-data-list">
                <div v-for="item in teamRankingBrief" :key="item.label" class="team-data-line">
                  <div>
                    <span>{{ item.label }}</span>
                    <b>{{ item.value }}</b>
                  </div>
                  <i :style="{ width: `${teamMetricRatio(item.value)}%` }"></i>
                </div>
              </div>
            </article>
          </div>

          <!-- 5. Core data: 基础表现 + 战术深度 -->
          <section class="team-data-two-col team-chart-data-grid">
            <article class="detail-card team-chart-card team-performance-card">
              <div class="panel-title-row">
                <h3>基础表现</h3>
                <span class="section-count">标准化对比</span>
              </div>
              <div ref="teamCoreChartRef" class="team-echart team-data-echart"></div>
              <div class="team-stat-chip-row">
                <span v-for="item in teamCoreSummaryStats" :key="item.label" class="team-stat-chip">
                  <small>{{ item.label }}</small>
                  <b>{{ item.value }}</b>
                </span>
              </div>
            </article>

            <article v-if="teamTacticalStats.length" class="detail-card team-chart-card team-performance-card">
              <div class="panel-title-row">
                <h3>战术深度</h3>
                <span class="section-count">攻防节奏</span>
              </div>
              <div ref="teamTacticalChartRef" class="team-echart team-data-echart"></div>
              <div class="team-stat-chip-row">
                <span v-for="item in teamTacticalSummaryStats" :key="item.label" class="team-stat-chip">
                  <small>{{ item.label }}</small>
                  <b>{{ item.value }}</b>
                </span>
              </div>
            </article>
          </section>

          <!-- 6. Match timeline -->
          <article class="detail-card team-timeline-card">
            <div class="panel-title-row">
              <h3>近期战绩时间线</h3>
              <span class="section-count">{{ teamRecentMatches.length }} 场</span>
            </div>
            <div v-if="!teamRecentMatches.length" class="empty-state">暂无比赛数据</div>
            <div v-else class="team-match-timeline">
              <div v-for="row in teamRecentMatches" :key="`${row.date}-${row.tournament}-${row.opponent}-${row.score}`" class="team-match-card">
                <div class="team-match-date">
                  <b>{{ row.date || '-' }}</b>
                  <span>{{ row.stage || row.tournament || '-' }}</span>
                </div>
                <div class="team-match-main">
                  <span class="team-with-logo team-side-a">
                    <span v-if="ensureCroppedLogo(row.teamLogo, row.teamName)" class="team-logo-badge-wrap">
                      <img class="team-logo-badge" :src="ensureCroppedLogo(row.teamLogo, row.teamName)" alt="" loading="lazy" @error="markBrokenImage" />
                    </span>
                    <span class="team-name-text">{{ row.teamName || teamDetail.basic.teamName || '-' }}</span>
                  </span>
                  <span class="team-match-score">{{ row.score || '-:-' }}</span>
                  <span class="team-with-logo team-side-b">
                    <span v-if="ensureCroppedLogo(row.opponentLogo, row.opponent)" class="team-logo-badge-wrap">
                      <img class="team-logo-badge" :src="ensureCroppedLogo(row.opponentLogo, row.opponent)" alt="" loading="lazy" @error="markBrokenImage" />
                    </span>
                    <span class="team-name-text">{{ row.opponent || '-' }}</span>
                  </span>
                </div>
                <span class="team-result-pill" :class="teamResultClass(row.result)">{{ row.result || '未知' }}</span>
              </div>
            </div>
          </article>

          <!-- 7. AI Portrait -->
          <article class="detail-card team-ai-brief-card">
            <div class="panel-title-row">
              <h3>战队画像</h3>
              <button class="link-btn" type="button" @click="triggerTeamAiAnalysis">生成 AI 解读</button>
            </div>
            <ol class="home-insight-list team-insight-list">
              <li v-for="item in teamInsightLines" :key="item">{{ item }}</li>
            </ol>
          </article>
        </div>
      </section>

      <section v-if="currentPage === 'player-detail'" class="section-card">
        <div class="section-topline">
          <h2>选手详情</h2>
          <button class="outline-btn" type="button" @click="navigateTo('players')">返回选手列表</button>
        </div>

        <div v-if="playerDetailState === 'loading'" class="empty-state">正在加载选手详情...</div>
        <div v-else-if="playerDetailState === 'error'" class="empty-state">加载失败：{{ playerDetailError }}</div>
        <div v-else-if="!playerDetail || !playerDetail.basic" class="empty-state">暂无详情数据</div>

        <div v-else class="player-detail-stack">
          <div class="hero-teammate-layout">
            <article class="player-hero-card">
              <div class="player-photo-stack" :style="teamLogoMaskStyle(playerTeamLogo)">
                <img v-if="usableImage(playerDetail.basic.avatar)" class="player-hero-avatar" :src="usableImage(playerDetail.basic.avatar)" alt="" loading="lazy" referrerpolicy="no-referrer" @error="markBrokenImage" />
                <b v-else class="player-hero-fallback">{{ playerInitial(playerDetail.basic.name) }}</b>
              </div>
              <div class="player-hero-text">
                <h3>{{ playerDetail.basic.name || '-' }}</h3>
                <p>
                  战队:
                  <button
                    v-if="playerCardTeamKey"
                    type="button"
                    class="player-team-link"
                    @click="openPlayerCardTeamDetail"
                  >
                    {{ playerDetail.basic.teamName || '-' }}
                  </button>
                  <span v-else>{{ playerDetail.basic.teamName || '-' }}</span>
                </p>
                <template v-if="isValorantGame">
                  <p>主要定位: {{ playerDetail.basic.primaryRole || playerDetail.basic.positions || playerDetail.basic.position || '-' }}</p>
                  <p>常用特工: {{ playerDetail.basic.agents || '-' }}</p>
                </template>
                <p v-else>位置: {{ playerDetail.basic.positions || playerDetail.basic.position || '-' }}</p>
              </div>
              <div class="player-hero-right">
                <template v-if="isLolGame">
                  <p>赛区排名: <b>{{ playerDetail.rank?.regionRank || playerDetail.basic.regionRank || '-' }}</b></p>
                  <p>总排名: <b>{{ playerDetail.rank?.globalRank || playerDetail.basic.globalRank || '-' }}</b></p>
                  <p>KDA: <b>{{ playerDetail.basic.kda || playerDetail.basic.rating || '-' }}</b></p>
                  <p>场次: <b>{{ playerDetail.basic.gamesPlayed || playerDetail.basic.impact || '-' }}</b></p>
                </template>
                <template v-else>
                  <p>Rating: <b>{{ playerDetail.basic.rating || '-' }}</b></p>
                  <p>Impact: <b>{{ playerDetail.basic.impact || '-' }}</b></p>
                  <p>KD: <b>{{ playerDetail.basic.kd || '-' }}</b></p>
                  <p>ADR: <b>{{ playerDetail.basic.adr || '-' }}</b></p>
                </template>
              </div>
            </article>

            <article class="detail-card teammate-panel teammate-panel-side teammate-panel-flat">
              <h3>现任队友</h3>
              <div v-if="!playerDetail.teammates?.length" class="empty-state">暂无数据</div>
              <div v-else class="teammate-grid" @wheel="onTeammateWheel">
                <button
                  v-for="row in playerDetail.teammates"
                  :key="row.teammate_id"
                  type="button"
                  class="teammate-item teammate-item-btn"
                  @click="navigateTo('player-detail', { playerId: row.teammate_id })"
                >
                  <div class="teammate-photo-stack" :style="teamLogoMaskStyle(resolveTeammateTeamLogo(row))">
                    <img v-if="resolveTeammateAvatar(row)" class="teammate-avatar" :src="resolveTeammateAvatar(row)" alt="" loading="lazy" referrerpolicy="no-referrer" @error="markBrokenImage" />
                    <b v-else class="teammate-avatar-fallback">{{ playerInitial(row.teammate_name || row.teammate_id) }}</b>
                  </div>
                  <div class="teammate-meta">
                    <p class="teammate-name">{{ row.teammate_name || row.teammate_id }}</p>
                    <img v-if="usableImage(row.country_logo)" class="teammate-flag" :src="usableImage(row.country_logo)" alt="" loading="lazy" referrerpolicy="no-referrer" @error="markBrokenImage" />
                  </div>
                </button>
              </div>
            </article>
          </div>

          <article v-if="!isLolGame && playerIdentityChips.length" class="detail-card player-profile-chip-card">
            <div class="panel-title-row">
              <h3>选手档案</h3>
              <span class="section-count">基础信息</span>
            </div>
            <div class="player-identity-chip-row player-identity-chip-grid">
              <span v-for="item in playerIdentityChips" :key="item.label"><small>{{ item.label }}</small><b>{{ item.value }}</b></span>
            </div>
          </article>

          <div v-if="isLolGame" class="ability-section-row">
            <article class="detail-card ability-pie-card">
              <h3>高阶能力分项</h3>
              <div v-if="!orderedAbilityMetrics.length" class="empty-state">暂无数据</div>
              <div v-else class="ability-pie-rows">
                <div class="ability-pie-row">
                  <div v-for="row in abilityMetricsTop" :key="`ability-top-${row.metric}`" class="ability-pie-item">
                    <div class="ability-pie-ring" :style="abilityRingStyle(row)">
                      <svg class="ability-pie-svg" viewBox="0 0 44 44" aria-hidden="true">
                        <circle class="ability-pie-track" cx="22" cy="22" r="15.9155" />
                        <circle class="ability-pie-progress" cx="22" cy="22" r="15.9155" />
                      </svg>
                      <div class="ability-pie-center">
                        <b>{{ row.value || '-' }}</b>
                      </div>
                    </div>
                    <span>{{ abilityMetricLabel(row.metric) }}</span>
                    <p class="ability-metric-extra">
                      <span>{{ metricAvgText(row) }}</span>
                      <b :class="`metric-delta-${metricDeltaInfo(row).cls}`">{{ metricDeltaInfo(row).text }}</b>
                    </p>
                  </div>
                </div>
                <div v-if="abilityMetricsBottom.length" class="ability-pie-row ability-pie-row-bottom">
                  <div v-for="row in abilityMetricsBottom" :key="`ability-bottom-${row.metric}`" class="ability-pie-item">
                    <div class="ability-pie-ring" :style="abilityRingStyle(row)">
                      <svg class="ability-pie-svg" viewBox="0 0 44 44" aria-hidden="true">
                        <circle class="ability-pie-track" cx="22" cy="22" r="15.9155" />
                        <circle class="ability-pie-progress" cx="22" cy="22" r="15.9155" />
                      </svg>
                      <div class="ability-pie-center">
                        <b>{{ row.value || '-' }}</b>
                      </div>
                    </div>
                    <span>{{ abilityMetricLabel(row.metric) }}</span>
                    <p class="ability-metric-extra">
                      <span>{{ metricAvgText(row) }}</span>
                      <b :class="`metric-delta-${metricDeltaInfo(row).cls}`">{{ metricDeltaInfo(row).text }}</b>
                    </p>
                  </div>
                </div>
              </div>
            </article>
          </div>

          <div v-else class="player-analytics-grid">
            <article class="detail-card player-analytics-card">
              <div class="panel-title-row">
                <h3>核心竞技表现</h3>
                <span class="section-count">雷达画像</span>
              </div>
              <div ref="playerAbilityChartRef" class="player-echart player-radar-echart"></div>
              <div v-if="playerProfileStats.length" class="player-stat-chip-row">
                <span v-for="item in playerProfileStats" :key="item.label" class="player-stat-chip">
                  <small>{{ item.label }}</small>
                  <b>{{ item.value }}</b>
                </span>
              </div>
            </article>

            <article class="detail-card player-analytics-card player-rating-card">
              <div class="panel-title-row">
                <h3>Rating 趋势</h3>
                <span class="section-count">状态走势</span>
              </div>
              <div v-if="!ratingSeries.length" class="empty-state">暂无数据</div>
              <template v-else>
                <div ref="playerRatingTrendChartRef" class="player-echart player-rating-echart"></div>
                <div class="rating-trend-summary-row">
                  <span v-for="item in ratingTrendSummary" :key="item.label" :class="`tone-${item.tone}`">
                    <small>{{ item.label }}</small>
                    <b>{{ item.value }}</b>
                  </span>
                </div>
                <p class="rating-trend-copy">{{ ratingTrendText }}</p>
              </template>
            </article>
          </div>

          <div v-if="isLolGame" class="lol-profile-row">
            <article class="detail-card lol-form-card">
              <h3>近期状态</h3>
              <div class="lol-form-grid">
                <p><span>近况局数</span><b>{{ lolRecentForm.games || 0 }}</b></p>
                <p><span>胜率</span><b>{{ lolRecentForm.winRate || '-' }}</b></p>
                <p><span>KDA</span><b>{{ lolRecentForm.kda || '-' }}</b></p>
                <p><span>场均 CS</span><b>{{ lolRecentForm.avgCs || '-' }}</b></p>
              </div>
              <div class="lol-form-champions">
                <span v-for="row in lolRecentForm.champions || []" :key="row.champion">{{ row.champion }} · {{ row.games }}</span>
              </div>
            </article>

            <article class="detail-card lol-career-card">
              <h3>生涯队伍</h3>
              <div v-if="lolCareerProfile.careerStartLabel || lolCareerProfile.source" class="lol-career-source">
                <span>职业首见: {{ lolCareerProfile.careerStartLabel || '-' }}</span>
                <b>{{ lolCareerProfile.source || '本站比赛库' }}</b>
              </div>
              <div v-if="!lolCareerTeams.length" class="empty-state">暂无数据</div>
              <div v-else class="lol-career-list">
                <div v-for="row in lolCareerTeams" :key="row.teamId" class="lol-career-item">
                  <img v-if="ensureCroppedLogo(row.teamLogo, row.teamName)" :src="ensureCroppedLogo(row.teamLogo, row.teamName)" alt="" loading="lazy" @error="markBrokenImage" />
                  <b v-else>{{ playerInitial(row.teamName) }}</b>
                  <p>
                    <strong>{{ row.teamName || '-' }}</strong>
                    <span>{{ row.role || '-' }} · {{ row.gamesLabel || `${row.games || 0} 局` }}</span>
                    <small v-if="row.sourceLabel">{{ row.sourceLabel }}</small>
                  </p>
                  <em>{{ row.tenureStart || row.firstSeen || '-' }} - {{ row.tenureEnd || row.lastSeen || '-' }}</em>
                </div>
              </div>
            </article>
          </div>

          <div v-else class="summary-settings-row">
            <article class="detail-card summary-chart-card">
              <h3>统计摘要</h3>
              <div class="summary-card-grid">
                <div v-for="item in summaryCards" :key="item.key" class="summary-stat-card">
                  <p class="summary-stat-label">{{ item.label }}</p>
                  <b class="summary-stat-value">{{ item.value }}</b>
                  <p class="summary-stat-hint">{{ item.hint }}</p>
                  <div class="summary-stat-track">
                    <i :style="{ width: `${item.percent}%` }"></i>
                  </div>
                </div>
              </div>
            </article>

            <article class="detail-card device-card device-card-rich">
              <h3>设备设置</h3>
              <div v-if="!playerDeviceGroups.length" class="empty-state">暂无数据</div>
              <div v-else class="device-group-grid">
                <div v-for="group in playerDeviceGroups" :key="group.key" class="device-group-card">
                  <strong>{{ group.title }}</strong>
                  <p v-for="item in group.rows" :key="item.label"><span>{{ item.label }}</span><b>{{ item.value }}</b></p>
                </div>
              </div>
            </article>
          </div>

          <div v-if="isLolGame" class="map-gear-row">
            <article class="detail-card map-panel-card lol-champion-card">
              <h3>英雄池表现</h3>
              <div class="panel-scroll mini-table">
                <div class="mini-head lol-champion-grid"><span>英雄</span><span>局数</span><span>KDA</span><span>K/D/A</span><span>场均 CS</span></div>
                <div v-if="!lolChampionStats.length" class="empty-state">暂无数据</div>
                <div v-for="row in lolChampionStats" :key="row.champion" class="mini-row lol-champion-grid">
                  <span>{{ row.champion || '-' }}</span>
                  <span>{{ row.games || '-' }}</span>
                  <span>{{ row.kda || '-' }}</span>
                  <span>{{ row.kills || 0 }}/{{ row.deaths || 0 }}/{{ row.assists || 0 }}</span>
                  <span>{{ row.avgCs || '-' }}</span>
                </div>
              </div>
            </article>
          </div>

          <div v-else class="map-gear-row player-map-gear-row">
            <article class="detail-card map-panel-card player-map-chart-card">
              <div class="panel-title-row">
                <h3>地图池表现</h3>
                <span class="section-count">Rating / KD / 场次</span>
              </div>
              <div v-if="!playerMapRows.length" class="empty-state">暂无数据</div>
              <template v-else>
                <div ref="playerMapChartRef" class="player-echart player-map-echart"></div>
                <div class="player-map-summary-row">
                  <span v-for="item in playerMapSummaryStats" :key="item.label"><small>{{ item.label }}</small><b>{{ item.value }}</b></span>
                </div>
              </template>
            </article>

            <article class="detail-card gear-panel-card">
              <h3>外设清单</h3>
              <div v-if="!playerDetail.equipment?.length" class="empty-state">暂无数据</div>
              <div v-else class="panel-scroll gear-grid gear-grid-paired">
                <div v-for="row in playerDetail.equipment" :key="row.category + row.name" class="gear-item">
                  <img v-if="resolveEquipmentLogo(row)" class="gear-logo" :src="resolveEquipmentLogo(row)" alt="" loading="lazy" referrerpolicy="no-referrer" @error="markBrokenImage" />
                  <b v-else class="gear-logo-fallback">{{ playerInitial(row.category || '?') }}</b>
                  <div class="gear-meta">
                    <span>{{ equipmentLabel(row.category) }}</span>
                    <b>{{ row.name || '-' }}</b>
                  </div>
                </div>
              </div>
            </article>
          </div>

          <article class="detail-card">
            <h3>近期比赛</h3>
            <div class="mini-table">
              <div v-if="isLolGame" class="mini-head lol-recent-grid"><span>时间</span><span>赛事</span><span>对手</span><span>英雄</span><span>K/D/A</span><span>CS</span><span>结果</span></div>
              <div v-else class="mini-head recent-grid player-recent-grid"><span>时间</span><span>赛事</span><span>对手</span><span>比分</span><span>Rating</span><span>ADR</span><span>KD</span><span></span></div>
              <div v-if="!playerRecentRows.length" class="empty-state">暂无数据</div>
              <template v-if="isLolGame">
                <div v-for="row in playerRecentRows" :key="row.game_id || row.match_id" class="mini-row lol-recent-grid">
                  <span>{{ row.ts_text || '-' }}</span>
                  <span>{{ row.tournament_name || row.event_name || '-' }}</span>
                  <span>{{ row.opponent_team_name || '-' }}</span>
                  <span>{{ row.champion || '-' }}</span>
                  <span>{{ row.kills ?? '-' }}/{{ row.deaths ?? '-' }}/{{ row.assists ?? '-' }}</span>
                  <span>{{ row.cs ?? '-' }}</span>
                  <span>{{ row.result || '-' }}</span>
                </div>
              </template>
              <template v-else>
                <button
                  v-for="row in playerRecentRows"
                  :key="row.match_id"
                  type="button"
                  class="mini-row recent-grid player-recent-grid player-recent-match-row"
                  :disabled="!(row.match_id || row.matchId)"
                  @click="openMatchDetail(row)"
                >
                  <span>{{ row.ts_text || '-' }}</span>
                  <span>{{ row.tournament_name || '-' }}</span>
                  <span>{{ row.opponent_team_name || '-' }}</span>
                  <span>{{ row.home_score ?? '-' }} - {{ row.opponent_score ?? '-' }}</span>
                  <span>{{ row.rating ?? '-' }}</span>
                  <span>{{ row.adr ?? '-' }}</span>
                  <span>{{ row.kd ?? '-' }}</span>
                  <span class="player-recent-link">详情</span>
                </button>
              </template>
            </div>
          </article>

          <article class="detail-card honor-card">
            <h3>历史荣誉</h3>
            <div v-if="!topHonors.length" class="empty-state">暂无数据</div>
            <template v-else>
              <div class="honor-scroll-list">
                <div v-for="(row, idx) in topHonors" :key="`${row.tt_id || 'tt'}-${row.start_time || idx}-${idx}`" class="honor-scroll-item">
                  <p><span>时间</span><b>{{ row.start_time || '-' }}</b></p>
                  <p><span>赛事</span><b>{{ row.tt_name || '-' }}</b></p>
                  <p><span>名次</span><b>{{ row.rank_desc || row.rank || '-' }}</b></p>
                  <p><span>战队</span><b>{{ row.team_name || '-' }}</b></p>
                  <p><span>级别</span><b>{{ row.grade || '-' }}</b></p>
                </div>
              </div>
            </template>
          </article>

          <article v-if="!isLolGame" class="detail-card">
            <h3>里程碑</h3>
            <div class="mini-table">
              <div class="mini-head milestone-grid"><span>时间</span><span>荣誉</span><span>详情</span><span>赛事</span></div>
              <div v-if="!playerDetail.milestones?.length" class="empty-state">暂无数据</div>
              <div v-for="row in playerDetail.milestones" :key="row.milestone_id + (row.created_at || '')" class="mini-row milestone-grid">
                <span>{{ row.created_at || row.achieve_time || '-' }}</span>
                <span>{{ row.honor_text || '-' }}</span>
                <span>{{ row.detail || '-' }}</span>
                <span>{{ row.tt_name || '-' }}</span>
              </div>
            </div>
          </article>

        </div>
      </section>
    </template>
  </div>

  <AiChatWidget
    ref="aiChatWidgetRef"
    :game-id="selectedGameId"
    :game-name="activeGameName"
    :page="currentPage"
    :context-data="aiContextData"
  />
</template>
