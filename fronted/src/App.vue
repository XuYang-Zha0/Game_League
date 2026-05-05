<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
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
import AiChatWidget from './components/AiChatWidget.vue'

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
const pageKeys = ['home', 'schedule', 'match-detail', 'tournaments', 'teams', 'players', 'player-detail', 'team-detail']

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
const isGameMenuOpen = ref(false)
const switchBlockRef = ref(null)
const aiChatWidgetRef = ref(null)
const lastAutoAnalyzedMatchId = ref('')

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
let scheduleLivePollTimer = null
let scheduleLivePollInFlight = false
let backendDatasetRefreshTimer = null
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
    ctx.currentMatch = matchDetail.value
  }
  if (currentPage.value === 'player-detail' && playerDetail.value) {
    ctx.currentPlayer = playerDetail.value
  }
  if (currentPage.value === 'team-detail' && teamDetail.value) {
    ctx.currentTeam = teamDetail.value
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

const teamDataCards = computed(() => {
  const rank = teamDetail.value?.rank || {}
  const stats = teamDetail.value?.stats || {}
  const from = (a, b) => {
    const v = a ?? b
    const text = String(v ?? '').trim()
    return text || '-'
  }
  if (isLolGame.value) {
    return [
      { label: '世界排名', value: from(rank.globalRank, stats.globalRank) },
      { label: '比赛数', value: from(stats.matchesPlayed, '-') },
      { label: '胜场', value: from(stats.wins, '-') },
      { label: '整体胜率', value: from(stats.winRate, '-') },
    ]
  }
  if (isValorantGame.value) {
    return [
      { label: '赛区排名', value: from(rank.regionRank, stats.regionRank) },
      { label: '全局参考', value: from(rank.globalRank, stats.globalRank) },
      { label: '评分', value: from(stats.rankScore, rank.score) },
      { label: '比赛数', value: from(stats.matchesPlayed, '-') },
      { label: '胜场', value: from(stats.wins, '-') },
      { label: '整体胜率', value: from(stats.winRate, '-') },
      { label: '评分模型', value: from(stats.model, '-') },
      { label: '近期状态', value: from(stats.status, '-') },
    ]
  }

  return [
    { label: '世界排名', value: from(rank.globalRank, stats.globalRank) },
    { label: 'Valve 排名', value: from(rank.valveRank, stats.valveRank) },
    { label: '评分', value: from(stats.rating, '-') },
    { label: '地图数', value: from(stats.mapNum, '-') },
    { label: '地图胜率', value: from(stats.mapWinRate, '-') },
    { label: '整体胜率', value: from(stats.winRate, '-') },
    { label: 'K/D', value: from(stats.kd, '-') },
    { label: '场均击杀', value: from(stats.avgKill, '-') },
    { label: '场均死亡', value: from(stats.avgDeath, '-') },
    { label: '场均助攻', value: from(stats.avgAssist, '-') },
    { label: '首杀率', value: from(stats.firstKillRate, '-') },
    { label: '总积分', value: from(rank.score, stats.score) },
  ]
})

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

const parseTierRank = (value) => {
  const text = normalizeTierText(value)
  if (!text) return null

  if (text.includes('MAJOR')) return 5
  if (text.includes('S+')) return 5
  if (text === 'S' || text.includes('S级')) return 4
  if (text === 'A' || text.includes('A级')) return 3
  if (text === 'B' || text.includes('B级')) return 2
  if (text === 'C' || text.includes('C级')) return 1

  return null
}

const tournamentTierByName = computed(() => {
  const map = new Map()
  for (const row of activeDataset.value?.tournaments || []) {
    const key = normalizeTournamentName(row?.name)
    if (!key) continue
    const tierText = String(row?.tier || row?.grade || row?.tierLevel || '').trim()
    if (!tierText) continue
    if (!map.has(key)) map.set(key, tierText)
  }
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
  if (directTier) return directTier

  const byTournament = tournamentTierByName.value.get(
    normalizeTournamentName(row?.tournament || row?.event_name || row?.name),
  )
  return String(byTournament || '').trim()
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
const animatedAbilityRatioMap = ref({})

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
  return { '--ability-value': `${clamp(ratio, 0, 100)}%` }
}

watch(
  orderedAbilityMetrics,
  async (rows) => {
    const seed = {}
    rows.forEach((row) => {
      seed[abilityMetricKey(row?.metric)] = 0
    })
    animatedAbilityRatioMap.value = seed
    await nextTick()
    requestAnimationFrame(() => {
      const next = {}
      rows.forEach((row) => {
        next[abilityMetricKey(row?.metric)] = abilityMetricRatio(row)
      })
      animatedAbilityRatioMap.value = next
    })
  },
  { immediate: true },
)

const ratingChartWidth = 860
const ratingChartHeight = 260
const ratingPadding = { top: 20, right: 20, bottom: 28, left: 24 }
const ratingHoverIndex = ref(-1)
const ratingChartRef = ref(null)

const ratingSeries = computed(() =>
  (playerDetail.value?.ratingChart || [])
    .slice(-60)
    .map((row) => ({
      date: String(row.date || ''),
      value: toNumber(row.rate),
    }))
    .filter((row) => row.date && Number.isFinite(row.value)),
)

const ratingBounds = computed(() => {
  if (!ratingSeries.value.length) {
    return { min: 0, max: 1, range: 1 }
  }
  let rawMin = Number.POSITIVE_INFINITY
  let rawMax = Number.NEGATIVE_INFINITY
  for (const point of ratingSeries.value) {
    rawMin = Math.min(rawMin, point.value)
    rawMax = Math.max(rawMax, point.value)
  }
  const rawRange = rawMax - rawMin
  const center = (rawMax + rawMin) / 2
  const minVisualSpan = 0.7
  const visualSpan = Math.max(rawRange * 1.65, minVisualSpan)

  let min = center - visualSpan / 2
  let max = center + visualSpan / 2

  if (min < 0) {
    max -= min
    min = 0
  }
  if (max <= min) {
    max = min + minVisualSpan
  }
  return { min, max, range: max - min }
})

const ratingPlotPoints = computed(() => {
  const points = ratingSeries.value
  if (!points.length) return []
  const plotWidth = ratingChartWidth - ratingPadding.left - ratingPadding.right
  const plotHeight = ratingChartHeight - ratingPadding.top - ratingPadding.bottom
  const step = points.length > 1 ? plotWidth / (points.length - 1) : 0
  return points.map((point, idx) => {
    const x = ratingPadding.left + step * idx
    const ratio = (point.value - ratingBounds.value.min) / ratingBounds.value.range
    const y = ratingPadding.top + (1 - ratio) * plotHeight
    return { ...point, x, y }
  })
})

const ratingLinePoints = computed(() =>
  ratingPlotPoints.value.map((point) => `${point.x},${point.y}`).join(' '),
)

const ratingActivePoint = computed(() => {
  const idx = ratingHoverIndex.value
  if (idx < 0) return null
  return ratingPlotPoints.value[idx] || null
})

const ratingAxis = computed(() => ({
  min: ratingBounds.value.min.toFixed(2),
  max: ratingBounds.value.max.toFixed(2),
}))

const onRatingChartMove = (event) => {
  if (!ratingChartRef.value || !ratingPlotPoints.value.length) return
  const rect = ratingChartRef.value.getBoundingClientRect()
  const rawX = event.clientX - rect.left
  const normalizedX = (rawX / rect.width) * ratingChartWidth
  let nearestIndex = 0
  let minDistance = Number.POSITIVE_INFINITY
  ratingPlotPoints.value.forEach((point, idx) => {
    const distance = Math.abs(point.x - normalizedX)
    if (distance < minDistance) {
      minDistance = distance
      nearestIndex = idx
    }
  })
  ratingHoverIndex.value = nearestIndex
}

const onRatingChartLeave = () => {
  ratingHoverIndex.value = -1
}

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

const parseRouteFromHash = (hash) => {
  const clean = hash.replace(/^#\/?/, '').trim()
  const segments = clean.split('/').filter(Boolean)
  const gameIdRaw = segments[0]
  const pageRaw = segments[1]
  const gameId = validGameIds.has(gameIdRaw) ? gameIdRaw : gameCatalog[0].id

  if (pageRaw === 'player') {
    const playerId = decodeURIComponent(segments.slice(2).join('/'))
    if (playerId) {
      return { gameId, page: 'player-detail', playerId, teamKey: '' }
    }
  }

  if (pageRaw === 'team') {
    const teamKey = decodeURIComponent(segments.slice(2).join('/'))
    if (teamKey) {
      return { gameId, page: 'team-detail', playerId: '', teamKey, matchId: '' }
    }
  }

  if (pageRaw === 'match') {
    const matchId = decodeURIComponent(segments.slice(2).join('/'))
    if (matchId) {
      return { gameId, page: 'match-detail', playerId: '', teamKey: '', matchId }
    }
  }

  return {
    gameId,
    page: pageKeys.includes(pageRaw) ? pageRaw : 'home',
    playerId: '',
    teamKey: '',
    matchId: '',
  }
}

const buildHash = (gameId, page, playerId = '', teamKey = '', matchId = '') => {
  if (page === 'player-detail' && playerId) {
    return `#/${gameId}/player/${encodeURIComponent(playerId)}`
  }
  if (page === 'team-detail' && teamKey) {
    return `#/${gameId}/team/${encodeURIComponent(teamKey)}`
  }
  if (page === 'match-detail' && matchId) {
    return `#/${gameId}/match/${encodeURIComponent(matchId)}`
  }
  return `#/${gameId}/${page}`
}

const syncFromHash = () => {
  const { gameId, page, playerId, teamKey, matchId } = parseRouteFromHash(window.location.hash)
  selectedGameId.value = gameId
  currentPage.value = page
  selectedPlayerId.value = playerId || ''
  selectedTeamKey.value = teamKey || ''
  selectedMatchId.value = matchId || ''
}

const navigateTo = (page, options = {}) => {
  isGameMenuOpen.value = false
  const hash = buildHash(
    selectedGameId.value,
    page,
    options.playerId || '',
    options.teamKey || '',
    options.matchId || '',
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

  visibleRows.sort((a, b) => {
    const ta = resolveMatchTimeMs(a)
    const tb = resolveMatchTimeMs(b)
    const aValid = Number.isFinite(ta)
    const bValid = Number.isFinite(tb)

    if (aValid && bValid) {
      if (scheduleViewMode.value === 'result') return tb - ta
      return ta - tb
    }
    if (aValid) return -1
    if (bValid) return 1

    const ad = resolveMatchDateText(a)
    const bd = resolveMatchDateText(b)
    if (scheduleViewMode.value === 'result') return bd.localeCompare(ad)
    return ad.localeCompare(bd)
  })

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
    return valorantHomePlayerSample(rows)
  }
  return rows.slice(0, 5)
})

const homeTopRanking = computed(() => {
  const rows = activeDataset.value?.leaderboard || []
  if (isValorantGame.value) {
    return groupRowsByRegion(rows, 'regionRank')
      .flatMap((group) => group.rows.slice(0, 1))
      .slice(0, 5)
  }
  return rows.slice(0, 5)
})
const homeMatchCount = computed(() => (activeDataset.value?.matches || []).length)
const homeHasData = computed(() => homeTopRanking.value.length > 0 || homeTopPlayers.value.length > 0)
const homeStatsCards = computed(() => [
  { key: 'tournaments', label: '赛事总数', value: String((activeDataset.value?.tournaments || []).length) },
  { key: 'matches', label: '比赛总数', value: String((activeDataset.value?.matches || []).length) },
  { key: 'teams', label: '战队总数', value: String((activeDataset.value?.teams || []).length) },
  { key: 'players', label: '选手总数', value: String((activeDataset.value?.players || []).length) },
])
const homeRecentMatches = computed(() => (activeDataset.value?.matches || []).slice(0, 6))
const homeFeaturedTournaments = computed(() => (activeDataset.value?.tournaments || []).slice(0, 6))

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
  const teamKey = String(row?.teamId || row?.team_id || row?.name || '').trim()
  if (!teamKey) return
  navigateTo('team-detail', { teamKey })
}

const openMatchDetail = (row) => {
  const matchId = String(row?.matchId || row?.match_id || '').trim()
  if (!matchId) return
  navigateTo('match-detail', { matchId })
}

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
  if (!['home', 'tournaments', 'teams', 'players', 'schedule'].includes(page)) return
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

watch([selectedGameId, currentPage, selectedPlayerId, selectedTeamKey, selectedMatchId], () => {
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

watch(
  () => playerDetail.value?.playerId,
  () => {
    ratingHoverIndex.value = -1
  },
)

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
  if (currentPage.value === 'player-detail' || currentPage.value === 'team-detail' || currentPage.value === 'match-detail') {
    currentPage.value = 'home'
    selectedPlayerId.value = ''
    selectedTeamKey.value = ''
    selectedMatchId.value = ''
  }
  navigateTo(currentPage.value, {
    playerId: selectedPlayerId.value,
    teamKey: selectedTeamKey.value,
    matchId: selectedMatchId.value,
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
  document.addEventListener('pointerover', handleTablePointerOver)
  window.addEventListener('mouseleave', handleTablePointerLeaveWindow)
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
  window.removeEventListener('hashchange', syncFromHash)
  document.removeEventListener('pointerdown', handleDocPointerDown)
  window.removeEventListener('keydown', handleWindowKeydown)
  document.removeEventListener('pointerover', handleTablePointerOver)
  window.removeEventListener('mouseleave', handleTablePointerLeaveWindow)
  clearTableHoverState()
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
      <section class="section-card home-search-card">
        <div class="section-topline">
          <h2>搜索</h2>
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

      <section class="section-card">
        <div class="section-topline">
          <h2>首页概览</h2>
          <span class="section-count">{{ homeMatchCount }} 场</span>
        </div>
        <div v-if="!homeHasData" class="empty-state">暂无首页数据，请先选择有数据的游戏</div>
        <div v-if="homeHasData" class="home-dual-grid">
          <article class="preview-panel">
            <h3>排名速览</h3>
            <div class="table-wrap">
              <div class="table-head home-rank-grid"><span>#</span><span>战队</span><span>赛区</span><span>胜率</span></div>
              <div v-for="row in homeTopRanking" :key="row.rank + row.name" class="table-row home-rank-grid">
                <span>{{ row.regionRank || row.rank }}</span><span>{{ row.name }}</span><span>{{ row.region }}</span><span>{{ row.winRate }}</span>
              </div>
            </div>
          </article>
          <article class="preview-panel">
            <h3>选手速览</h3>
            <div class="table-wrap">
              <div class="table-head home-player-grid"><span>选手</span><span>战队</span><span>角色</span><span>{{ playerPrimaryMetricLabel }}</span></div>
              <div v-for="row in homeTopPlayers" :key="row.playerId" class="table-row home-player-grid">
                <span class="player-cell">
                  <img v-if="usableImage(row.avatar)" class="player-avatar" :src="usableImage(row.avatar)" alt="" loading="lazy" referrerpolicy="no-referrer" @error="markBrokenImage" />
                  <b v-else class="player-avatar-fallback">{{ playerInitial(row.name) }}</b>
                  <button class="link-btn player-name" type="button" @click="openPlayerDetail(row)">{{ row.name }}</button>
                </span>
                <span>{{ row.team }}</span><span>{{ row.role }}</span><span>{{ playerPrimaryMetricValue(row) }}</span>
              </div>
            </div>
          </article>
        </div>

        <div v-if="homeHasData" class="quick-grid" style="margin-top: 10px;">
          <article v-for="item in homeStatsCards" :key="item.key" class="quick-card">
            <p class="quick-title">{{ item.label }}</p>
            <p><strong>{{ item.value }}</strong></p>
          </article>
        </div>

        <div v-if="homeHasData" class="focus-grid" style="margin-top: 10px;">
          <article class="focus-card">
            <h3>近期比赛</h3>
            <ul>
              <li v-if="!homeRecentMatches.length">暂无数据</li>
              <li v-for="row in homeRecentMatches" :key="`${row.date}-${row.tournament}-${row.teamA}-${row.teamB}`">
                {{ row.date || '-' }} · {{ row.teamA || '-' }} vs {{ row.teamB || '-' }} · {{ row.score || '-' }}
              </li>
            </ul>
          </article>
          <article class="focus-card">
            <h3>热门赛事</h3>
            <ul>
              <li v-if="!homeFeaturedTournaments.length">暂无数据</li>
              <li v-for="row in homeFeaturedTournaments" :key="`${row.name}-${row.start}`">
                {{ row.name || '-' }} · {{ row.start || '-' }} · {{ row.status || '-' }}
              </li>
            </ul>
          </article>
        </div>
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
              <div v-for="row in group.rows" :key="row.name + row.start" class="table-row tournament-grid">
                <span>{{ row.name }}</span><span>{{ row.tier }}</span><span>{{ row.region }}</span><span>{{ row.start || '-' }}</span><span>{{ row.end || '-' }}</span><span>{{ row.status }}</span>
              </div>
            </div>
          </section>
        </div>

        <div v-else class="table-wrap tournament-scroll-wrap" @scroll="handleTournamentScroll">
          <div class="table-head tournament-grid tournament-head-sticky"><span>赛事</span><span>级别</span><span>赛区</span><span>开始日期</span><span>结束日期</span><span>状态</span></div>
          <div v-if="!visibleTournamentRows.length" class="empty-state">暂无匹配数据</div>
          <div v-for="row in visibleTournamentRows" :key="row.name + row.start" class="table-row tournament-grid">
            <span>{{ row.name }}</span><span>{{ row.tier }}</span><span>{{ row.region }}</span><span>{{ row.start || '-' }}</span><span>{{ row.end || '-' }}</span><span>{{ row.status }}</span>
          </div>
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
              <div class="table-head player-grid"><span>赛区排名</span><span>选手</span><span>所属战队</span><span>位置</span><span>{{ playerPrimaryMetricLabel }}</span><span>{{ playerSecondaryMetricLabel }}</span><span>指标</span></div>
              <div v-for="row in group.rows" :key="row.playerKey || row.playerId" class="table-row player-grid">
                <span>{{ row.displayRank || row.rank || '-' }}</span>
                <span class="player-cell">
                  <img v-if="usableImage(row.avatar)" class="player-avatar" :src="usableImage(row.avatar)" alt="" loading="lazy" referrerpolicy="no-referrer" @error="markBrokenImage" />
                  <b v-else class="player-avatar-fallback">{{ playerInitial(row.name) }}</b>
                  <button class="link-btn player-name" type="button" @click="openPlayerDetail(row)">{{ row.name }}</button>
                </span>
                <span>{{ row.team }}</span><span>{{ row.role }}</span><span>{{ playerPrimaryMetricValue(row) }}</span><span>{{ playerSecondaryMetricValue(row) }}</span><span>{{ row.highlight }}</span>
              </div>
            </div>
          </section>
          <button v-if="hasMorePlayerRows" class="outline-btn load-more-btn" type="button" @click="loadMorePlayerRows">加载更多</button>
        </div>

        <div v-else class="table-wrap player-scroll-wrap" @scroll.passive="handlePlayerScroll">
          <div class="table-head player-grid"><span>排名</span><span>选手</span><span>所属战队</span><span>角色</span><span>{{ playerPrimaryMetricLabel }}</span><span>{{ playerSecondaryMetricLabel }}</span><span>指标</span></div>
          <div v-if="!playerRowsWithRank.length" class="empty-state">暂无匹配数据</div>
          <div v-for="row in visiblePlayerRowsWithRank" :key="row.playerKey || row.playerId" class="table-row player-grid">
            <span>{{ row.displayRank }}</span>
            <span class="player-cell">
              <img v-if="usableImage(row.avatar)" class="player-avatar" :src="usableImage(row.avatar)" alt="" loading="lazy" referrerpolicy="no-referrer" @error="markBrokenImage" />
              <b v-else class="player-avatar-fallback">{{ playerInitial(row.name) }}</b>
              <button class="link-btn player-name" type="button" @click="openPlayerDetail(row)">{{ row.name }}</button>
            </span>
            <span>{{ row.team }}</span><span>{{ row.role }}</span><span>{{ playerPrimaryMetricValue(row) }}</span><span>{{ playerSecondaryMetricValue(row) }}</span><span>{{ row.highlight }}</span>
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

        <div v-else class="team-detail-stack">
          <article class="team-hero-card">
            <div class="team-hero-left">
              <span class="team-hero-logo-wrap" v-if="ensureCroppedLogo(teamDetail.basic.teamLogo, teamDetail.basic.teamName)">
                <img
                  class="team-hero-logo"
                  :src="ensureCroppedLogo(teamDetail.basic.teamLogo, teamDetail.basic.teamName)"
                  alt=""
                  loading="lazy"
                  @error="markBrokenImage"
                />
              </span>
              <b v-else class="team-hero-fallback">{{ playerInitial(teamDetail.basic.teamName) }}</b>
              <div class="team-hero-text">
                <h3>{{ teamDetail.basic.teamName || '-' }}</h3>
                <p>赛区: {{ teamDetail.basic.region || '-' }}</p>
                <p>战队成员: {{ teamDetail.members?.length || 0 }} / 5</p>
              </div>
            </div>
            <div class="team-hero-right">
              <template v-if="isLolGame">
                <p>比赛数: <b>{{ displayValue(teamDetail.stats?.matchesPlayed) }}</b></p>
                <p>胜场: <b>{{ displayValue(teamDetail.stats?.wins) }}</b></p>
                <p>胜率: <b>{{ displayValue(teamDetail.stats?.winRate) }}</b></p>
                <p>本站评分: <b>{{ displayValue(teamDetail.stats?.rankScore || teamDetail.rank?.score) }}</b></p>
              </template>
              <template v-else-if="isValorantGame">
                <p>赛区排名: <b>{{ displayValue(teamDetail.rank?.regionRank ?? teamDetail.stats?.regionRank) }}</b></p>
                <p>全局参考: <b>{{ displayValue(teamDetail.rank?.globalRank ?? teamDetail.stats?.globalRank) }}</b></p>
                <p>区域评分: <b>{{ displayValue(teamDetail.stats?.rankScore || teamDetail.rank?.score) }}</b></p>
                <p>胜率: <b>{{ displayValue(teamDetail.stats?.winRate) }}</b></p>
              </template>
              <template v-else>
                <p>世界排名: <b>{{ displayValue(teamDetail.rank?.globalRank ?? teamDetail.stats?.globalRank) }}</b></p>
                <p>Valve 排名: <b>{{ displayValue(teamDetail.rank?.valveRank ?? teamDetail.stats?.valveRank) }}</b></p>
                <p>评分: <b>{{ displayValue(teamDetail.stats?.rating) }}</b></p>
                <p>K/D: <b>{{ displayValue(teamDetail.stats?.kd) }}</b></p>
              </template>
            </div>
          </article>

          <article class="detail-card">
            <div class="team-tab-row">
              <button
                type="button"
                class="team-tab-btn"
                :class="{ active: teamDetailTab === 'data' }"
                @click="teamDetailTab = 'data'"
              >
                数据
              </button>
              <button
                type="button"
                class="team-tab-btn"
                :class="{ active: teamDetailTab === 'matches' }"
                @click="teamDetailTab = 'matches'"
              >
                近期比赛
              </button>
            </div>

            <div v-if="teamDetailTab === 'data'" class="team-data-grid">
              <div v-for="item in teamDataCards" :key="item.label" class="team-data-item">
                <span>{{ item.label }}</span>
                <b>{{ item.value }}</b>
              </div>
            </div>

            <div v-else class="mini-table">
              <div class="mini-head team-match-grid"><span>时间</span><span>赛事</span><span>对阵</span><span>比分</span><span>结果</span></div>
              <div v-if="!teamDetail.recentMatches?.length" class="empty-state">暂无比赛数据</div>
              <div v-for="row in teamDetail.recentMatches" :key="`${row.date}-${row.tournament}-${row.opponent}-${row.score}`" class="mini-row team-match-grid">
                <span>{{ row.date || '-' }}</span>
                <span>{{ row.tournament || '-' }}</span>
                <span class="matchup-cell">
                  <span class="team-with-logo team-side-a">
                    <span v-if="ensureCroppedLogo(row.teamLogo, row.teamName)" class="team-logo-badge-wrap">
                      <img class="team-logo-badge" :src="ensureCroppedLogo(row.teamLogo, row.teamName)" alt="" loading="lazy" @error="markBrokenImage" />
                    </span>
                    <span class="team-name-text">{{ row.teamName || '-' }}</span>
                  </span>
                  <span class="vs-text">vs</span>
                  <span class="team-with-logo team-side-b">
                    <span v-if="ensureCroppedLogo(row.opponentLogo, row.opponent)" class="team-logo-badge-wrap">
                      <img class="team-logo-badge" :src="ensureCroppedLogo(row.opponentLogo, row.opponent)" alt="" loading="lazy" @error="markBrokenImage" />
                    </span>
                    <span class="team-name-text">{{ row.opponent || '-' }}</span>
                  </span>
                </span>
                <span>{{ row.score || '-' }}</span>
                <span>{{ row.result || '-' }}</span>
              </div>
            </div>
          </article>

          <article class="detail-card">
            <h3>战队成员</h3>
            <div class="team-member-grid">
              <button
                v-for="row in teamMembers"
                :key="row.playerId"
                type="button"
                class="team-member-card"
                :class="{ disabled: row.isPlaceholder || !row.playerId }"
                :disabled="row.isPlaceholder || !row.playerId"
                @click="!row.isPlaceholder && row.playerId && navigateTo('player-detail', { playerId: row.playerId })"
              >
                <img v-if="usableImage(row.avatar)" class="team-member-avatar" :src="usableImage(row.avatar)" alt="" loading="lazy" referrerpolicy="no-referrer" @error="markBrokenImage" />
                <b v-else class="team-member-avatar-fallback">{{ playerInitial(row.name) }}</b>
                <div class="team-member-meta">
                  <p class="team-member-name">{{ row.name || '-' }}</p>
                  <p class="team-member-extra">{{ row.position || '-' }}</p>
                </div>
              </button>
            </div>
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

          <div class="ability-section-row">
            <article class="detail-card ability-pie-card">
              <h3>{{ isLolGame ? '高阶能力分项' : '能力分项' }}</h3>
              <div v-if="!orderedAbilityMetrics.length" class="empty-state">暂无数据</div>
              <div v-else class="ability-pie-rows">
                <div class="ability-pie-row">
                  <div v-for="row in abilityMetricsTop" :key="`ability-top-${row.metric}`" class="ability-pie-item">
                    <div class="ability-pie-ring" :style="abilityRingStyle(row)">
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

            <article class="detail-card device-card">
              <h3>设备设置</h3>
              <p>鼠标: {{ playerDetail.mouseConfig?.mouse_name || '-' }}</p>
              <p>DPI / eDPI: {{ playerDetail.mouseConfig?.dpi ?? '-' }} / {{ playerDetail.mouseConfig?.e_dpi ?? '-' }}</p>
              <p>灵敏度: {{ playerDetail.mouseConfig?.sensitivity ?? '-' }}</p>
              <p>分辨率: {{ playerDetail.monitorConfig?.resolution || '-' }}</p>
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

          <div v-else class="map-gear-row">
            <article class="detail-card map-panel-card">
              <h3>地图表现</h3>
              <div class="panel-scroll mini-table">
                <div class="mini-head map-grid"><span>地图</span><span>KD</span><span>Rating</span><span>场次</span></div>
                <div v-if="!playerDetail.maps?.length" class="empty-state">暂无数据</div>
                <div v-for="row in playerDetail.maps" :key="row.map_name" class="mini-row map-grid">
                  <span class="map-name-badge" :style="mapBadgeStyle(row.map_name)">{{ formatMapName(row.map_name) }}</span>
                  <span>{{ row.map_kd }}</span>
                  <span>{{ row.map_rating }}</span>
                  <span>{{ row.use_num }}</span>
                </div>
              </div>
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
              <div v-else class="mini-head recent-grid"><span>时间</span><span>赛事</span><span>对手</span><span>比分</span></div>
              <div v-if="!playerDetail.recentMatches?.length" class="empty-state">暂无数据</div>
              <template v-if="isLolGame">
                <div v-for="row in playerDetail.recentMatches" :key="row.game_id || row.match_id" class="mini-row lol-recent-grid">
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
                <div v-for="row in playerDetail.recentMatches" :key="row.match_id" class="mini-row recent-grid">
                  <span>{{ row.ts_text || '-' }}</span>
                  <span>{{ row.tournament_name || '-' }}</span>
                  <span>{{ row.opponent_team_name || '-' }}</span>
                  <span>{{ row.home_score ?? '-' }} - {{ row.opponent_score ?? '-' }}</span>
                </div>
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

          <article v-if="!isLolGame" class="detail-card rating-chart-card">
            <h3>Rating 曲线</h3>
            <div v-if="!ratingPlotPoints.length" class="empty-state">暂无数据</div>
            <div
              v-else
              class="rating-chart-wrap"
            >
              <svg
                ref="ratingChartRef"
                class="rating-chart"
                :viewBox="`0 0 ${ratingChartWidth} ${ratingChartHeight}`"
                preserveAspectRatio="none"
                @mousemove="onRatingChartMove"
                @mouseleave="onRatingChartLeave"
              >
                <polyline class="rating-line" :points="ratingLinePoints" />
                <line
                  v-if="ratingActivePoint"
                  class="rating-guide"
                  :x1="ratingActivePoint.x"
                  :x2="ratingActivePoint.x"
                  :y1="ratingPadding.top"
                  :y2="ratingChartHeight - ratingPadding.bottom"
                />
                <circle
                  v-for="(point, idx) in ratingPlotPoints"
                  :key="`${point.date}-${idx}`"
                  class="rating-point"
                  :class="{ active: idx === ratingHoverIndex }"
                  :cx="point.x"
                  :cy="point.y"
                  :r="idx === ratingHoverIndex ? 4.8 : 3.4"
                />
              </svg>

              <div
                v-if="ratingActivePoint"
                class="rating-tooltip"
                :style="{ left: `${(ratingActivePoint.x / ratingChartWidth) * 100}%` }"
              >
                <p>{{ ratingActivePoint.date }}</p>
                <p>Rating: {{ ratingActivePoint.value.toFixed(2) }}</p>
              </div>
              <div class="rating-axis-labels">
                <span>Min {{ ratingAxis.min }}</span>
                <span>Max {{ ratingAxis.max }}</span>
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
