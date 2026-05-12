<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts/core'

const props = defineProps({
  gameId: { type: String, default: 'cs2' },
  state: { type: String, default: 'idle' },
  error: { type: String, default: '' },
  detail: { type: Object, default: null },
  ensureCroppedLogo: {
    type: Function,
    default: (logo) => String(logo || '').trim(),
  },
  resolveMapImage: {
    type: Function,
    default: () => '',
  },
  formatMapName: {
    type: Function,
    default: (name) => String(name || '-').trim() || '-',
  },
  imageErrorHandler: { type: Function, default: null },
})

defineEmits(['back'])

const hasMaps = (detail) => Array.isArray(detail?.maps) && detail.maps.length > 0

const teamScore = (team) => {
  if (team?.score === null || team?.score === undefined) return '-'
  return String(team.score)
}

const normalizeMapIndex = (value) => {
  const num = Number.parseInt(String(value ?? ''), 10)
  return Number.isFinite(num) ? num : 0
}

const valueOrDash = (value) => {
  const text = String(value ?? '').trim()
  return text || '-'
}

const toMetricNumber = (value) => {
  const text = String(value ?? '').replace('%', '').trim()
  const num = Number.parseFloat(text)
  return Number.isFinite(num) ? num : Number.NaN
}

const formatNumber = (value, digits = 2, suffix = '') => {
  if (!Number.isFinite(value)) return '-'
  return `${value.toFixed(digits)}${suffix}`
}

const signedNumber = (value) => {
  if (!Number.isFinite(value)) return '-'
  return `${value > 0 ? '+' : ''}${value}`
}

const mapRows = computed(() => (Array.isArray(props.detail?.maps) ? props.detail.maps : []))
const mapPlayerStats = computed(() =>
  Array.isArray(props.detail?.mapPlayerStats) ? props.detail.mapPlayerStats : [],
)

const selectedMapIndex = ref(0)
const selectedRoundNumber = ref(0)
const matchMvpChartRef = ref(null)
let matchMvpChart = null

watch(
  () => props.detail,
  (detail) => {
    const rows = Array.isArray(detail?.maps) ? detail.maps : []
    if (!rows.length) {
      selectedMapIndex.value = 0
      return
    }
    const firstIdx = normalizeMapIndex(rows[0]?.index) || 1
    selectedMapIndex.value = firstIdx
  },
  { immediate: true },
)

const selectedMapRow = computed(() => {
  if (!mapRows.value.length) return null
  const idx = normalizeMapIndex(selectedMapIndex.value)
  return mapRows.value.find((row) => normalizeMapIndex(row?.index) === idx) || mapRows.value[0]
})

const hasMapScopedStats = computed(() =>
  mapPlayerStats.value.some((item) => {
    const a = Array.isArray(item?.teamA) ? item.teamA.length : 0
    const b = Array.isArray(item?.teamB) ? item.teamB.length : 0
    return a + b > 0
  }),
)

const selectedMapPlayers = computed(() => {
  if (!hasMapScopedStats.value) return null
  const idx = normalizeMapIndex(selectedMapRow.value?.index)
  return mapPlayerStats.value.find((item) => normalizeMapIndex(item?.mapIndex) === idx) || null
})

const selectedMapName = computed(() => {
  if (selectedMapRow.value?.map) return selectedMapRow.value.map
  if (selectedMapPlayers.value?.mapName) return selectedMapPlayers.value.mapName
  return '-'
})

const fallbackTeamAPlayers = computed(() =>
  Array.isArray(props.detail?.playerStats?.teamA) ? props.detail.playerStats.teamA : [],
)
const fallbackTeamBPlayers = computed(() =>
  Array.isArray(props.detail?.playerStats?.teamB) ? props.detail.playerStats.teamB : [],
)

const teamAPlayers = computed(() => {
  if (hasMapScopedStats.value) {
    if (!selectedMapPlayers.value) return []
    return Array.isArray(selectedMapPlayers.value.teamA) ? selectedMapPlayers.value.teamA : []
  }
  return fallbackTeamAPlayers.value
})

const teamBPlayers = computed(() => {
  if (hasMapScopedStats.value) {
    if (!selectedMapPlayers.value) return []
    return Array.isArray(selectedMapPlayers.value.teamB) ? selectedMapPlayers.value.teamB : []
  }
  return fallbackTeamBPlayers.value
})

const hasAnyPlayerStats = computed(() => teamAPlayers.value.length > 0 || teamBPlayers.value.length > 0)
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

const isBrokenImage = (value) => imageKeysFor(value).some((key) => brokenImageMap.value[key])

const usableImage = (value) => {
  const src = String(value || '').trim()
  return src && !isBrokenImage(src) ? src : ''
}

const handleImageError = (event) => {
  const target = event?.currentTarget || event?.target
  const keys = [
    ...imageKeysFor(target?.getAttribute?.('src')),
    ...imageKeysFor(target?.currentSrc),
    ...imageKeysFor(target?.src),
  ]
  if (keys.length) {
    const next = { ...brokenImageMap.value }
    for (const key of keys) next[key] = true
    brokenImageMap.value = next
  }
  if (typeof props.imageErrorHandler === 'function') props.imageErrorHandler(event)
}

const teamALogo = computed(() =>
  usableImage(
    props.ensureCroppedLogo?.(props.detail?.teamA?.logo, props.detail?.teamA?.name) ||
      '',
  ),
)
const teamBLogo = computed(() =>
  usableImage(
    props.ensureCroppedLogo?.(props.detail?.teamB?.logo, props.detail?.teamB?.name) ||
      '',
  ),
)

const playerAvatarSrc = (player) => {
  const avatar = String(player?.avatar ?? '').trim()
  if (usableImage(avatar)) return avatar
  return ''
}

const playerInitial = (player) => {
  const name = String(player?.name ?? '').trim()
  return name ? name.slice(0, 1).toUpperCase() : '?'
}

const resolveMapImageSrc = (mapName) => String(props.resolveMapImage?.(mapName) || '').trim()
const resolveMapLabel = (mapName) =>
  String(props.formatMapName?.(mapName) || mapName || '-').trim() || '-'
const mapBadgeStyle = (mapName) => {
  const src = resolveMapImageSrc(mapName)
  if (!src) return {}
  return { '--map-bg': `url("${src}")` }
}

const onSelectMap = (row) => {
  const idx = normalizeMapIndex(row?.index)
  if (idx > 0) selectedMapIndex.value = idx
}

const scoreText = (row) => {
  const a = row?.team1Score
  const b = row?.team2Score
  if (a === null || a === undefined || b === null || b === undefined) return '-'
  return `${a}:${b}`
}

const winnerText = (row, detail) => {
  if (row?.winner === 'team1') return detail?.teamA?.name || '-'
  if (row?.winner === 'team2') return detail?.teamB?.name || '-'
  if (row?.winner === 'draw') return '平局'
  return '-'
}

const kdaText = (player) => {
  const hasK = player?.kill !== null && player?.kill !== undefined
  const hasD = player?.death !== null && player?.death !== undefined
  const hasA = player?.assist !== null && player?.assist !== undefined
  if (!hasK && !hasD && !hasA) return '-'
  const k = hasK ? String(player.kill) : '-'
  const d = hasD ? String(player.death) : '-'
  const a = hasA ? String(player.assist) : '-'
  return `${k}/${d}/${a}`
}

const playerKdDiff = (player) => {
  const diff = toMetricNumber(player?.kdDiff)
  if (Number.isFinite(diff)) return signedNumber(diff)
  const kill = Number.parseInt(String(player?.kill ?? ''), 10)
  const death = Number.parseInt(String(player?.death ?? ''), 10)
  if (Number.isFinite(kill) && Number.isFinite(death)) return signedNumber(kill - death)
  return '-'
}

const bilibiliLive = computed(() => props.detail?.bilibiliLive || props.detail?.tournament?.bilibiliLive || null)
const bilibiliReplay = computed(() => props.detail?.bilibiliReplay || null)
const selectedReplayPage = ref(1)
const matchHasLiveBadge = computed(() => Boolean(bilibiliLive.value?.supported))
const matchHasReplayBadge = computed(() => Boolean(bilibiliReplay.value?.supported))
const matchLiveStatusText = computed(() => {
  if (!matchHasLiveBadge.value) return ''
  if (String(bilibiliLive.value?.status || '').trim().toLowerCase() === 'live') return '正在直播'
  return '有直播'
})
const replayEpisodes = computed(() => (Array.isArray(bilibiliReplay.value?.episodes) ? bilibiliReplay.value.episodes : []))
const activeReplayEpisode = computed(() => {
  if (!replayEpisodes.value.length) return null
  const selected = Number.parseInt(String(selectedReplayPage.value || ''), 10)
  return replayEpisodes.value.find((item) => Number.parseInt(String(item?.page || ''), 10) === selected) || replayEpisodes.value[0]
})
const replayEpisodeCount = computed(() => replayEpisodes.value.length || Number.parseInt(String(bilibiliReplay.value?.episodeCount || '0'), 10) || 0)
const matchReplayStatusText = computed(() => {
  if (!matchHasReplayBadge.value) return ''
  if (!bilibiliReplay.value?.hasVideo) return '官方未匹配'
  if (replayEpisodeCount.value > 1) return `已匹配 ${replayEpisodeCount.value} 集`
  return '官方已匹配'
})
const replayVideoUrl = computed(() => String(activeReplayEpisode.value?.videoUrl || bilibiliReplay.value?.videoUrl || '').trim())
const replayEmbedUrl = computed(() => String(activeReplayEpisode.value?.embedUrl || activeReplayEpisode.value?.pageEmbedUrl || bilibiliReplay.value?.embedUrl || bilibiliReplay.value?.pageEmbedUrl || '').trim())
const replaySearchUrl = computed(() => String(bilibiliReplay.value?.searchUrl || '').trim())
const replayTitle = computed(() => String(bilibiliReplay.value?.title || '').trim() || 'B 站官方比赛回放')
const replayMatchedTermsText = computed(() => (Array.isArray(bilibiliReplay.value?.matchedTerms) ? bilibiliReplay.value.matchedTerms.filter(Boolean).join(' / ') : ''))
const replayCurrentLabel = computed(() => {
  const label = String(activeReplayEpisode.value?.label || activeReplayEpisode.value?.part || '').trim()
  if (label) return label
  const page = Number.parseInt(String(activeReplayEpisode.value?.page || ''), 10)
  return Number.isFinite(page) && page > 0 ? `第${page}局` : ''
})
const replayMetaText = computed(() => {
  const parts = [
    String(bilibiliReplay.value?.sourceLabel || '').trim(),
    String(bilibiliReplay.value?.uploader || '').trim(),
    String(bilibiliReplay.value?.publishedAt || '').trim(),
    replayCurrentLabel.value ? `当前播放：${replayCurrentLabel.value}` : '',
  ].filter(Boolean)
  return parts.join(' · ')
})
const replayEmptyText = computed(() => {
  if (!matchHasReplayBadge.value) return ''
  return '当前未从 CSGO官方赛事近期视频中匹配到本场官方回放。'
})
const selectReplayEpisode = (episode) => {
  const page = Number.parseInt(String(episode?.page || ''), 10)
  if (Number.isFinite(page) && page > 0) selectedReplayPage.value = page
}
const openReplayTab = () => {
  const url = replayVideoUrl.value || replaySearchUrl.value
  if (!url || typeof window === 'undefined') return
  window.open(url, '_blank', 'noopener,noreferrer')
}
const openReplayPopup = () => {
  const url = replayVideoUrl.value || replaySearchUrl.value
  if (!url || typeof window === 'undefined') return
  window.open(url, 'bilibili-replay-video', 'popup=yes,width=1440,height=900,resizable=yes,scrollbars=yes')
}

watch(
  [bilibiliReplay, selectedMapIndex],
  ([replay]) => {
    const episodes = Array.isArray(replay?.episodes) ? replay.episodes : []
    if (!episodes.length) {
      selectedReplayPage.value = 1
      return
    }
    const currentMapPage = normalizeMapIndex(selectedMapIndex.value)
    const mapped = episodes.find((item) => normalizeMapIndex(item?.mapIndex || item?.page) === currentMapPage)
    if (mapped) {
      selectedReplayPage.value = normalizeMapIndex(mapped.page) || 1
      return
    }
    selectedReplayPage.value = normalizeMapIndex(replay?.defaultEpisode?.page || episodes[0]?.page) || 1
  },
  { immediate: true },
)
watch(activeReplayEpisode, (episode) => {
  const idx = normalizeMapIndex(episode?.mapIndex || episode?.page)
  if (idx > 0 && idx !== normalizeMapIndex(selectedMapIndex.value)) selectedMapIndex.value = idx
})


const matchMetaChips = computed(() => [
  { label: '开赛时间', value: props.detail?.matchTime || props.detail?.date || '-' },
  { label: '赛事', value: props.detail?.tournament?.name || '-' },
  { label: '级别', value: props.detail?.tournament?.tier || '-' },
  { label: '状态', value: props.detail?.statusText || '-' },
  { label: '赛制', value: props.detail?.bo ? `BO${props.detail.bo}` : '-' },
])

const mapSummaryCards = computed(() => [
  { label: '地图总数', value: mapRows.value.length || '-' },
  { label: '当前地图', value: resolveMapLabel(selectedMapName.value) },
  { label: '总比分', value: props.detail?.score || '-' },
  { label: '胜者', value: props.detail?.winner || '-' },
])

const roundEventMaps = computed(() => (Array.isArray(props.detail?.roundEvents) ? props.detail.roundEvents : []))
const selectedRoundEventMap = computed(() => {
  if (!roundEventMaps.value.length) return null
  const idx = normalizeMapIndex(selectedMapIndex.value)
  return roundEventMaps.value.find((item) => normalizeMapIndex(item?.mapIndex) === idx) || roundEventMaps.value[0]
})
const selectedRoundEvents = computed(() => (Array.isArray(selectedRoundEventMap.value?.rounds) ? selectedRoundEventMap.value.rounds : []))
const selectedRoundEventCount = computed(() => selectedRoundEvents.value.reduce((sum, round) => sum + (Array.isArray(round?.events) ? round.events.length : 0), 0))
const activeRound = computed(() => {
  if (!selectedRoundEvents.value.length) return null
  return selectedRoundEvents.value.find((round) => normalizeMapIndex(round?.roundNumber) === normalizeMapIndex(selectedRoundNumber.value)) || selectedRoundEvents.value[0]
})
const activeRoundEvents = computed(() => (Array.isArray(activeRound.value?.events) ? activeRound.value.events : []))
const roundEventSummaryCards = computed(() => [
  { label: '总事件', value: props.detail?.roundEventSummary?.eventCount || selectedRoundEventCount.value || '-' },
  { label: '总回合', value: props.detail?.roundEventSummary?.roundCount || '-' },
  { label: '当前地图回合', value: selectedRoundEvents.value.length || '-' },
  { label: '当前地图事件', value: selectedRoundEventCount.value || '-' },
])

watch(selectedRoundEvents, (rounds) => {
  const firstRound = normalizeMapIndex(rounds?.[0]?.roundNumber)
  if (!rounds?.length) {
    selectedRoundNumber.value = 0
    return
  }
  const exists = rounds.some((round) => normalizeMapIndex(round?.roundNumber) === normalizeMapIndex(selectedRoundNumber.value))
  if (!exists) selectedRoundNumber.value = firstRound
}, { immediate: true })

const selectRound = (round) => {
  const num = normalizeMapIndex(round?.roundNumber)
  if (num > 0) selectedRoundNumber.value = num
}

const eventTypeLabel = (type) => ({
  round_start: '开始',
  round_end: '结束',
  kill: '击杀',
  bomb_planted: '下包',
  player_join: '加入',
  player_quit: '离开',
  match_started: '地图',
}[String(type || '').trim()] || '事件')

const roundWinnerText = (round) => {
  const winner = String(round?.winnerSide || '').trim()
  const winType = String(round?.winType || '').trim()
  const ct = round?.scoreCt
  const t = round?.scoreT
  const score = ct !== null && ct !== undefined && t !== null && t !== undefined ? `${ct}:${t}` : ''
  return [winner ? `${winner} 胜` : '', score, winType].filter(Boolean).join(' · ') || '进行记录'
}

const normalizePlayerName = (value) => String(value || '').trim().toLowerCase()

const teamAPlayerNameSet = computed(() => new Set([
  ...fallbackTeamAPlayers.value,
  ...mapPlayerStats.value.flatMap((block) => (Array.isArray(block?.teamA) ? block.teamA : [])),
].map((player) => normalizePlayerName(player?.name)).filter(Boolean)))

const teamBPlayerNameSet = computed(() => new Set([
  ...fallbackTeamBPlayers.value,
  ...mapPlayerStats.value.flatMap((block) => (Array.isArray(block?.teamB) ? block.teamB : [])),
].map((player) => normalizePlayerName(player?.name)).filter(Boolean)))

const playerTeamSide = (name) => {
  const key = normalizePlayerName(name)
  if (!key) return ''
  if (teamAPlayerNameSet.value.has(key)) return 'teamA'
  if (teamBPlayerNameSet.value.has(key)) return 'teamB'
  return ''
}

const eventTimelineSide = (event) => {
  const primary = playerTeamSide(event?.playerName)
  if (primary) return primary
  return playerTeamSide(event?.relatedPlayerName)
}

const roundEventSideForTeam = (teamSide, roundNumber) => {
  const side = String(teamSide || '').trim().toLowerCase()
  const round = normalizeMapIndex(roundNumber)
  if (!side || !round) return ''
  if (round <= 12) return side
  if (side === 'ct') return 't'
  if (side === 't') return 'ct'
  return ''
}

const mapInitialSideByTeam = computed(() => {
  const result = { teamA: '', teamB: '' }
  for (const round of selectedRoundEvents.value) {
    for (const event of Array.isArray(round?.events) ? round.events : []) {
      const side = roundEventSideForTeam(event?.teamSide, round?.roundNumber)
      if (!side) continue
      const team = eventTimelineSide(event)
      if (team && !result[team]) result[team] = side
      if (result.teamA && result.teamB) return result
    }
  }
  if (result.teamA && !result.teamB) result.teamB = result.teamA === 'ct' ? 't' : 'ct'
  if (result.teamB && !result.teamA) result.teamA = result.teamB === 'ct' ? 't' : 'ct'
  return result
})

const activeRoundTeamSide = (team) => {
  const initial = mapInitialSideByTeam.value[team]
  const round = normalizeMapIndex(activeRound.value?.roundNumber)
  if (!initial || !round) return ''
  if (round <= 12) return initial.toUpperCase()
  return initial === 'ct' ? 'T' : 'CT'
}

const filteredRoundEvents = computed(() => activeRoundEvents.value.filter((event) => !['round_start', 'round_end'].includes(event?.eventType)))
const orderedRoundEvents = computed(() => filteredRoundEvents.value.map((event) => ({
  ...event,
  timelineSide: eventTimelineSide(event) || 'teamA',
})))

const playerBadge = (name, side) => {
  const safe = String(name || '').trim()
  if (!safe) return '<span class="mp-badge">?</span>'
  return `<span class="mp-badge mp-${side}">${safe}</span>`
}

const eventDisplayHtml = (event) => {
  const type = String(event?.eventType || '').trim()
  const player = String(event?.playerName || '').trim()
  const related = String(event?.relatedPlayerName || '').trim()
  const weapon = String(event?.weapon || '').trim()
  const site = String(event?.bombSite || '').trim()
  const assister = String(event?.assisterName || '').trim()
  const side = event?.timelineSide || 'teamA'
  const oppSide = side === 'teamA' ? 'teamB' : 'teamA'

  if (type === 'kill') {
    const parts = [playerBadge(player, side)]
    if (assister) parts.push(` <small>+${playerBadge(assister, side)} 助攻</small>`)
    if (weapon) parts.push(`--${weapon}`)
    parts.push(`kill--&gt; ${playerBadge(related, oppSide)}`)
    return parts.join(' ')
  }
  if (type === 'bomb_planted') {
    return `${playerBadge(player, side)} -下包${site ? ` ${site}` : ''}-`
  }
  if (type === 'player_join') return `${playerBadge(player, side)} -加入-`
  if (type === 'player_quit') return `${playerBadge(player, side)} -离开-`
  return event?.eventText || '-'
}

const roundWinnerSide = (round) => {
  const events = Array.isArray(round?.events) ? round.events : []
  const endIndex = events.findIndex((event) => event?.eventType === 'round_end')
  const priorEvents = (endIndex >= 0 ? events.slice(0, endIndex) : events).filter((event) => !['round_start', 'round_end'].includes(event?.eventType))
  const alive = priorEvents.reduce((state, event) => {
    const actorSide = eventTimelineSide(event)
    const victimSide = playerTeamSide(event?.relatedPlayerName)
    if (actorSide) state[actorSide] = true
    if (victimSide) state[victimSide] = false
    return state
  }, { teamA: true, teamB: true })
  if (alive.teamA && !alive.teamB) return 'teamA'
  if (alive.teamB && !alive.teamA) return 'teamB'
  const lastEvent = [...priorEvents].reverse().find((event) => eventTimelineSide(event))
  return lastEvent ? eventTimelineSide(lastEvent) : ''
}

const roundWinnerLogo = (round) => {
  const side = roundWinnerSide(round)
  return side === 'teamA' ? teamALogo.value : side === 'teamB' ? teamBLogo.value : ''
}

const roundWinnerName = (round) => {
  const side = roundWinnerSide(round)
  return side === 'teamA' ? props.detail?.teamA?.name || '-' : side === 'teamB' ? props.detail?.teamB?.name || '-' : '-'
}

const averageMetric = (players, key) => {
  const values = players.map((player) => toMetricNumber(player?.[key])).filter(Number.isFinite)
  if (!values.length) return Number.NaN
  return values.reduce((sum, value) => sum + value, 0) / values.length
}

const sumMetric = (players, key) =>
  players.reduce((sum, player) => {
    const value = Number.parseInt(String(player?.[key] ?? ''), 10)
    return Number.isFinite(value) ? sum + value : sum
  }, 0)

const teamMetricSummary = (players) => {
  const kill = sumMetric(players, 'kill')
  const death = sumMetric(players, 'death')
  return {
    rating: averageMetric(players, 'rating'),
    adr: averageMetric(players, 'adr'),
    kast: averageMetric(players, 'kast'),
    kdDiff: kill || death ? kill - death : Number.NaN,
  }
}

const selectedMapTeamSummary = computed(() => ({
  teamA: teamMetricSummary(teamAPlayers.value),
  teamB: teamMetricSummary(teamBPlayers.value),
}))

const compareWidth = (value, max) => {
  if (!Number.isFinite(value) || !Number.isFinite(max) || max <= 0) return '0%'
  return `${Math.max(8, Math.min(100, (Math.max(0, value) / max) * 100)).toFixed(1)}%`
}

const teamCompareRows = computed(() => {
  const a = selectedMapTeamSummary.value.teamA
  const b = selectedMapTeamSummary.value.teamB
  return [
    { label: '平均 Rating', left: a.rating, right: b.rating, leftText: formatNumber(a.rating), rightText: formatNumber(b.rating), digits: 2 },
    { label: '平均 ADR', left: a.adr, right: b.adr, leftText: formatNumber(a.adr, 1), rightText: formatNumber(b.adr, 1), digits: 1 },
    { label: 'K-D 差值', left: a.kdDiff, right: b.kdDiff, leftText: signedNumber(a.kdDiff), rightText: signedNumber(b.kdDiff), digits: 0 },
    { label: '平均 KAST', left: a.kast, right: b.kast, leftText: formatNumber(a.kast, 1, '%'), rightText: formatNumber(b.kast, 1, '%'), digits: 1 },
  ].map((row) => {
    const min = Math.min(Number.isFinite(row.left) ? row.left : 0, Number.isFinite(row.right) ? row.right : 0, 0)
    const leftShifted = (Number.isFinite(row.left) ? row.left : 0) - min
    const rightShifted = (Number.isFinite(row.right) ? row.right : 0) - min
    const max = Math.max(leftShifted, rightShifted)
    return {
      ...row,
      leftWidth: compareWidth(leftShifted, max),
      rightWidth: compareWidth(rightShifted, max),
      leader: row.left > row.right ? 'teamA' : row.right > row.left ? 'teamB' : 'draw',
    }
  })
})

const allCurrentPlayers = computed(() => [
  ...teamAPlayers.value.map((player) => ({ ...player, side: 'teamA', teamName: props.detail?.teamA?.name || '-' })),
  ...teamBPlayers.value.map((player) => ({ ...player, side: 'teamB', teamName: props.detail?.teamB?.name || '-' })),
])

const topPlayers = computed(() =>
  [...allCurrentPlayers.value]
    .map((player) => ({ ...player, ratingValue: toMetricNumber(player?.rating), adrValue: toMetricNumber(player?.adr) }))
    .sort((a, b) => (Number.isFinite(b.ratingValue) ? b.ratingValue : -1) - (Number.isFinite(a.ratingValue) ? a.ratingValue : -1))
    .slice(0, 4),
)

const winningSide = computed(() => {
  const a = props.detail?.teamA?.score
  const b = props.detail?.teamB?.score
  if (a === null || a === undefined || b === null || b === undefined) return ''
  if (Number(a) > Number(b)) return 'teamA'
  if (Number(b) > Number(a)) return 'teamB'
  return ''
})

const winningTeamName = computed(() =>
  winningSide.value === 'teamA' ? props.detail?.teamA?.name || '-' : winningSide.value === 'teamB' ? props.detail?.teamB?.name || '-' : '-',
)

const fullMatchPlayers = computed(() => {
  const teamA = fallbackTeamAPlayers.value.map((player) => ({ ...player, side: 'teamA', teamName: props.detail?.teamA?.name || '-' }))
  const teamB = fallbackTeamBPlayers.value.map((player) => ({ ...player, side: 'teamB', teamName: props.detail?.teamB?.name || '-' }))
  if (teamA.length + teamB.length) return [...teamA, ...teamB]

  const grouped = new Map()
  for (const block of mapPlayerStats.value) {
    for (const side of ['teamA', 'teamB']) {
      const rows = Array.isArray(block?.[side]) ? block[side] : []
      for (const player of rows) {
        const key = `${side}::${player?.playerId || player?.name || ''}`
        if (!grouped.has(key)) {
          grouped.set(key, {
            ...player,
            side,
            teamName: side === 'teamA' ? props.detail?.teamA?.name || '-' : props.detail?.teamB?.name || '-',
            ratingValues: [],
            adrValues: [],
            kastValues: [],
            kprValues: [],
            kdValues: [],
            swingValues: [],
            killTotal: 0,
            deathTotal: 0,
            assistTotal: 0,
            maps: 0,
          })
        }
        const item = grouped.get(key)
        item.maps += 1
        for (const [field, bucket] of [['rating', 'ratingValues'], ['adr', 'adrValues'], ['kast', 'kastValues'], ['kpr', 'kprValues'], ['kd', 'kdValues'], ['swing', 'swingValues']]) {
          const num = toMetricNumber(player?.[field])
          if (Number.isFinite(num)) item[bucket].push(num)
        }
        item.killTotal += Number.parseInt(String(player?.kill ?? '0'), 10) || 0
        item.deathTotal += Number.parseInt(String(player?.death ?? '0'), 10) || 0
        item.assistTotal += Number.parseInt(String(player?.assist ?? '0'), 10) || 0
      }
    }
  }

  return [...grouped.values()].map((player) => {
    const avg = (values, digits = 2) => (values.length ? (values.reduce((sum, value) => sum + value, 0) / values.length).toFixed(digits) : '')
    return {
      ...player,
      rating: avg(player.ratingValues),
      adr: avg(player.adrValues, 1),
      kast: player.kastValues.length ? `${avg(player.kastValues, 1)}%` : '',
      kpr: avg(player.kprValues),
      kd: avg(player.kdValues),
      swing: avg(player.swingValues),
      kill: player.killTotal,
      death: player.deathTotal,
      assist: player.assistTotal,
      kdDiff: player.killTotal || player.deathTotal ? String(player.killTotal - player.deathTotal) : '',
    }
  })
})

const matchMvpPlayer = computed(() => {
  const candidates = fullMatchPlayers.value
    .filter((player) => player.side === winningSide.value)
    .map((player) => ({ ...player, ratingValue: toMetricNumber(player?.rating), adrValue: toMetricNumber(player?.adr) }))
  if (!candidates.length) return null
  return candidates.sort((a, b) => {
    const ratingDelta = (Number.isFinite(b.ratingValue) ? b.ratingValue : -1) - (Number.isFinite(a.ratingValue) ? a.ratingValue : -1)
    if (ratingDelta) return ratingDelta
    return (Number.isFinite(b.adrValue) ? b.adrValue : -1) - (Number.isFinite(a.adrValue) ? a.adrValue : -1)
  })[0]
})

const mvpPlayer = computed(() => matchMvpPlayer.value || topPlayers.value[0] || null)

const metricRatio = (value, average, goodEnd, lowerBetter = false) => {
  const num = toMetricNumber(value)
  if (!Number.isFinite(num)) return 0
  if (lowerBetter) {
    const low = Number(goodEnd)
    const high = Number(average)
    if (!Number.isFinite(low) || !Number.isFinite(high) || high === low) return 0
    return Math.max(0, Math.min(100, ((high - num) / (high - low)) * 100))
  }
  const avg = Number(average)
  const good = Number(goodEnd)
  if (!Number.isFinite(avg) || !Number.isFinite(good) || good === avg) return 0
  return Math.max(0, Math.min(100, ((num - avg) / (good - avg)) * 45 + 55))
}

const matchMvpMetrics = computed(() => {
  const player = matchMvpPlayer.value
  if (!player) return []
  const swingValue = valueOrDash(player.swing)
  const sixthMetric = swingValue !== '-'
    ? { label: 'Swing', value: swingValue, ratio: metricRatio(player.swing, 0, 20) }
    : { label: 'Assist', value: valueOrDash(player.assist), ratio: metricRatio(player.assist, 0, 12) }
  return [
    { label: 'Rating', value: valueOrDash(player.rating), ratio: metricRatio(player.rating, 1, 1.45) },
    { label: 'ADR', value: valueOrDash(player.adr), ratio: metricRatio(player.adr, 75, 115) },
    { label: 'KAST', value: valueOrDash(player.kast), ratio: metricRatio(player.kast, 70, 90) },
    { label: 'KPR', value: valueOrDash(player.kpr), ratio: metricRatio(player.kpr, 0.7, 1) },
    { label: 'KD', value: valueOrDash(player.kd), ratio: metricRatio(player.kd, 1, 1.8) },
    sixthMetric,
  ]
})

const matchMvpChartOption = computed(() => ({
  backgroundColor: 'transparent',
  tooltip: {
    trigger: 'item',
    formatter: () => matchMvpMetrics.value.map((row) => `${row.label}：${row.value}`).join('<br/>'),
  },
  radar: {
    radius: '66%',
    indicator: matchMvpMetrics.value.map((row) => ({ name: row.label, max: 100 })),
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
          name: '全场 MVP',
          value: matchMvpMetrics.value.map((row) => row.ratio),
          areaStyle: { color: 'rgba(80, 143, 255, 0.26)' },
          lineStyle: { color: '#6ea8ff', width: 2.4 },
          itemStyle: { color: '#ffffff', borderColor: '#6ea8ff', borderWidth: 2 },
        },
      ],
    },
  ],
}))

const renderMatchMvpChart = async () => {
  await nextTick()
  if (!matchMvpChartRef.value || !matchMvpMetrics.value.length) {
    matchMvpChart?.dispose()
    matchMvpChart = null
    return
  }
  matchMvpChart ||= echarts.init(matchMvpChartRef.value)
  matchMvpChart.setOption(matchMvpChartOption.value, true)
}

const resizeMatchMvpChart = () => matchMvpChart?.resize()
const disposeMatchMvpChart = () => {
  matchMvpChart?.dispose()
  matchMvpChart = null
}

watch([() => props.detail, matchMvpMetrics], () => {
  renderMatchMvpChart()
}, { deep: true })

onMounted(() => {
  renderMatchMvpChart()
  window.addEventListener('resize', resizeMatchMvpChart)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', resizeMatchMvpChart)
  disposeMatchMvpChart()
})

const isTeamWinner = (side) => {
  const a = props.detail?.teamA?.score
  const b = props.detail?.teamB?.score
  if (a === null || a === undefined || b === null || b === undefined) return false
  return side === 'teamA' ? Number(a) > Number(b) : Number(b) > Number(a)
}
</script>

<template>
  <section class="section-card match-detail-section">
    <div class="section-topline">
      <h2>比赛详情</h2>
      <button type="button" class="match-detail-back-btn" @click="$emit('back')">返回赛程</button>
    </div>

    <div v-if="state === 'loading'" class="empty-state">正在加载比赛详情...</div>
    <div v-else-if="state === 'error'" class="empty-state">{{ error || '比赛详情加载失败' }}</div>
    <div v-else-if="!detail || detail.exists === false" class="empty-state">未找到该比赛的数据库记录</div>
    <div v-else class="match-detail-wrap match-report-wrap">
      <article class="match-report-hero">
        <div class="match-report-team" :class="{ winner: isTeamWinner('teamA') }">
          <span class="match-report-logo-wrap">
            <img v-if="teamALogo" :src="teamALogo" alt="" loading="lazy" @error="handleImageError" />
            <b v-else>{{ String(detail.teamA?.name || '-').slice(0, 1) }}</b>
          </span>
          <p>
            <small>{{ isTeamWinner('teamA') ? 'WINNER' : 'TEAM A' }}</small>
            <strong>{{ detail.teamA?.name || '-' }}</strong>
          </p>
        </div>

        <div class="match-report-score-core">
          <span>{{ detail.bo ? `BO${detail.bo}` : 'MATCH' }}</span>
          <div>
            <b>{{ teamScore(detail.teamA) }}</b>
            <i>:</i>
            <b>{{ teamScore(detail.teamB) }}</b>
          </div>
          <p>{{ detail.statusText || '-' }}</p>
        </div>

        <div class="match-report-team team-b" :class="{ winner: isTeamWinner('teamB') }">
          <p>
            <small>{{ isTeamWinner('teamB') ? 'WINNER' : 'TEAM B' }}</small>
            <strong>{{ detail.teamB?.name || '-' }}</strong>
          </p>
          <span class="match-report-logo-wrap">
            <img v-if="teamBLogo" :src="teamBLogo" alt="" loading="lazy" @error="handleImageError" />
            <b v-else>{{ String(detail.teamB?.name || '-').slice(0, 1) }}</b>
          </span>
        </div>

        <div class="match-report-chip-row">
          <span v-for="item in matchMetaChips" :key="item.label">
            <small>{{ item.label }}</small>
            <b>{{ item.value }}</b>
          </span>
          <span v-if="matchHasLiveBadge" class="match-live-chip" :class="{ live: bilibiliLive?.status === 'live' }">
            <small>B 站直播</small>
            <b>
              <i v-if="bilibiliLive?.status === 'live'" class="bilibili-live-dot"></i>
              {{ matchLiveStatusText }}
            </b>
          </span>
          <span v-if="matchHasReplayBadge" class="match-replay-chip" :class="{ ready: bilibiliReplay?.hasVideo }">
            <small>B 站回放</small>
            <b>{{ matchReplayStatusText }}</b>
          </span>
        </div>
      </article>

      <div class="match-summary-strip">
        <article v-for="item in mapSummaryCards" :key="item.label" class="summary-stat-card">
          <span>{{ item.label }}</span>
          <b>{{ item.value }}</b>
        </article>
      </div>

      <div class="match-report-grid">
        <article class="detail-card match-map-report-card">
          <div class="panel-title-row">
            <h3>分图战报</h3>
            <span>{{ mapRows.length }} 张地图</span>
          </div>
          <div v-if="!hasMaps(detail)" class="empty-state">暂无分图数据</div>
          <div v-else class="match-map-card-grid">
            <button
              v-for="row in mapRows"
              :key="`${row.index}-${row.map}`"
              type="button"
              class="match-map-card"
              :class="{ active: normalizeMapIndex(row.index) === normalizeMapIndex(selectedMapIndex) }"
              :style="mapBadgeStyle(row.map)"
              @click="onSelectMap(row)"
            >
              <span>MAP {{ row.index || '-' }}</span>
              <strong>{{ resolveMapLabel(row.map) }}</strong>
              <b>{{ scoreText(row) }}</b>
              <small>{{ winnerText(row, detail) }}</small>
            </button>
          </div>
        </article>

        <article class="detail-card match-team-compare-card">
          <div class="panel-title-row">
            <h3>团队表现对比</h3>
            <span>{{ resolveMapLabel(selectedMapName) }}</span>
          </div>
          <div v-if="!hasAnyPlayerStats" class="empty-state">暂无选手统计可用于对比</div>
          <div v-else class="match-compare-list">
            <div v-for="row in teamCompareRows" :key="row.label" class="match-compare-row">
              <div class="match-compare-row-head">
                <b :class="{ leader: row.leader === 'teamA' }">{{ row.leftText }}</b>
                <span>{{ row.label }}</span>
                <b :class="{ leader: row.leader === 'teamB' }">{{ row.rightText }}</b>
              </div>
              <div class="match-compare-bars">
                <i class="left" :style="{ width: row.leftWidth }"></i>
                <i class="right" :style="{ width: row.rightWidth }"></i>
              </div>
            </div>
          </div>
        </article>
      </div>

      <article class="detail-card match-player-report-card">
        <div class="panel-title-row match-player-title-row">
          <div>
            <h3>选手战报</h3>
            <span>当前地图：{{ resolveMapLabel(selectedMapName) }}</span>
          </div>
          <div v-if="mvpPlayer" class="match-mvp-pill">
            <small>MVP</small>
            <b>{{ mvpPlayer.name || '-' }}</b>
            <span>{{ valueOrDash(mvpPlayer.rating) }}</span>
          </div>
        </div>

        <div v-if="!hasAnyPlayerStats" class="empty-state">暂无选手数据</div>
        <template v-else>
          <div class="match-top-player-row">
            <span v-for="(player, idx) in topPlayers" :key="`${player.playerId || player.name || idx}-top`">
              <small>#{{ idx + 1 }} · {{ player.teamName }}</small>
              <b>{{ player.name || '-' }}</b>
              <em>Rating {{ valueOrDash(player.rating) }} / ADR {{ valueOrDash(player.adr) }}</em>
            </span>
          </div>

          <div class="match-player-table-grid">
            <div class="match-player-team-table">
              <strong class="match-player-team-title">{{ detail.teamA?.name || 'Team A' }}</strong>
              <div
                v-for="(row, idx) in teamAPlayers"
                :key="`a-row-${row.playerId || row.name || idx}`"
                class="match-player-row-card"
              >
                <span class="match-player-avatar">
                  <img v-if="playerAvatarSrc(row)" :src="playerAvatarSrc(row)" alt="" loading="lazy" referrerpolicy="no-referrer" @error="handleImageError" />
                  <b v-else>{{ playerInitial(row) }}</b>
                </span>
                <p>
                  <strong>{{ row.name || '-' }}</strong>
                  <small>{{ row.countryName || row.teamName || '-' }}</small>
                </p>
                <span><small>Rating</small><b>{{ valueOrDash(row.rating) }}</b></span>
                <span><small>ADR</small><b>{{ valueOrDash(row.adr) }}</b></span>
                <span><small>KAST</small><b>{{ valueOrDash(row.kast) }}</b></span>
                <span><small>K-D</small><b>{{ playerKdDiff(row) }}</b></span>
                <span><small>K/D/A</small><b>{{ kdaText(row) }}</b></span>
              </div>
            </div>

            <div class="match-player-team-table">
              <strong class="match-player-team-title">{{ detail.teamB?.name || 'Team B' }}</strong>
              <div
                v-for="(row, idx) in teamBPlayers"
                :key="`b-row-${row.playerId || row.name || idx}`"
                class="match-player-row-card"
              >
                <span class="match-player-avatar">
                  <img v-if="playerAvatarSrc(row)" :src="playerAvatarSrc(row)" alt="" loading="lazy" referrerpolicy="no-referrer" @error="handleImageError" />
                  <b v-else>{{ playerInitial(row) }}</b>
                </span>
                <p>
                  <strong>{{ row.name || '-' }}</strong>
                  <small>{{ row.countryName || row.teamName || '-' }}</small>
                </p>
                <span><small>Rating</small><b>{{ valueOrDash(row.rating) }}</b></span>
                <span><small>ADR</small><b>{{ valueOrDash(row.adr) }}</b></span>
                <span><small>KAST</small><b>{{ valueOrDash(row.kast) }}</b></span>
                <span><small>K-D</small><b>{{ playerKdDiff(row) }}</b></span>
                <span><small>K/D/A</small><b>{{ kdaText(row) }}</b></span>
              </div>
            </div>
          </div>
        </template>
      </article>

      <article class="detail-card match-full-mvp-card">
        <div class="panel-title-row">
          <h3>全场 MVP</h3>
          <span>{{ winningTeamName }}</span>
        </div>
        <div v-if="!matchMvpPlayer" class="empty-state">暂无胜者方选手数据</div>
        <div v-else class="match-full-mvp-layout">
          <div class="match-full-mvp-profile">
            <span class="match-full-mvp-avatar">
              <img v-if="playerAvatarSrc(matchMvpPlayer)" :src="playerAvatarSrc(matchMvpPlayer)" alt="" loading="lazy" referrerpolicy="no-referrer" @error="handleImageError" />
              <b v-else>{{ playerInitial(matchMvpPlayer) }}</b>
            </span>
            <p>
              <small>{{ winningTeamName }}</small>
              <strong>{{ matchMvpPlayer.name || '-' }}</strong>
              <em>全场平均 Rating {{ valueOrDash(matchMvpPlayer.rating) }}</em>
            </p>
          </div>
          <div ref="matchMvpChartRef" class="match-full-mvp-chart"></div>
          <div class="match-full-mvp-metrics">
            <span v-for="item in matchMvpMetrics" :key="item.label">
              <small>{{ item.label }}</small>
              <b>{{ item.value }}</b>
            </span>
          </div>
        </div>
      </article>

      <article v-if="matchHasReplayBadge" class="detail-card match-replay-card">
        <div class="panel-title-row match-replay-title-row">
          <div>
            <h3>B 站比赛回放</h3>
            <span>{{ replayTitle }}</span>
          </div>
          <span>{{ bilibiliReplay?.hasVideo ? (replayEpisodeCount > 1 ? `已匹配 ${replayEpisodeCount} 集官方回放` : '已匹配官方视频') : '待匹配官方视频' }}</span>
        </div>
        <div class="match-replay-layout">
          <div v-if="bilibiliReplay?.hasVideo && replayEmbedUrl" class="match-replay-player-wrap">
            <div v-if="replayEpisodes.length > 1" class="schedule-view-toggle match-replay-episode-toggle">
              <button
                v-for="episode in replayEpisodes"
                :key="episode.page || episode.label || episode.part"
                type="button"
                class="schedule-view-btn match-replay-episode-btn"
                :class="{ active: Number.parseInt(String(activeReplayEpisode?.page || ''), 10) === Number.parseInt(String(episode?.page || ''), 10) }"
                @click="selectReplayEpisode(episode)"
              >
                {{ episode.label || episode.part || `第${episode.page}局` }}
              </button>
            </div>
            <iframe
              class="match-replay-frame"
              :src="replayEmbedUrl"
              :title="replayCurrentLabel ? `B 站比赛回放 - ${replayCurrentLabel}` : 'B 站比赛回放'"
              allowfullscreen
              loading="lazy"
              referrerpolicy="strict-origin-when-cross-origin"
            ></iframe>
          </div>
          <div v-else class="empty-state match-replay-empty-state">{{ replayEmptyText }}</div>
          <div class="match-replay-side-panel">
            <div class="match-replay-meta-card">
              <strong>{{ replayTitle }}</strong>
              <small>{{ replayMetaText || 'B 站官方赛事回放入口' }}</small>
              <small v-if="replayMatchedTermsText">匹配命中：{{ replayMatchedTermsText }}</small>
            </div>
            <div v-if="replayEpisodes.length > 1 && replayCurrentLabel" class="match-replay-meta-card match-replay-episode-meta">
              <small>当前分图：{{ replayCurrentLabel }}</small>
            </div>
            <div v-if="!bilibiliReplay?.hasVideo && replayMatchedTermsText" class="match-replay-meta-card">
              <small>最近一次匹配线索：{{ replayMatchedTermsText }}</small>
            </div>
            <div class="match-replay-action-row">
              <button type="button" class="outline-btn match-replay-action-btn" @click="openReplayPopup">小窗打开</button>
              <button type="button" class="outline-btn match-replay-action-btn" @click="openReplayTab">新标签打开</button>
            </div>
            <div class="match-replay-link-stack">
              <a v-if="replayVideoUrl" :href="replayVideoUrl" target="_blank" rel="noreferrer">打开当前回放视频</a>
              <a v-else-if="replaySearchUrl" :href="replaySearchUrl" target="_blank" rel="noreferrer">前往 B 站搜索本场回放</a>
              <a v-if="bilibiliReplay?.uploaderUrl" :href="bilibiliReplay.uploaderUrl" target="_blank" rel="noreferrer">打开 CSGO 官方赛事视频主页</a>
            </div>
          </div>
        </div>
      </article>

      <article class="detail-card match-round-log-card">
        <div class="panel-title-row match-round-map-head">
          <div>
            <h3>回合记录</h3>
            <span>{{ selectedRoundEventMap?.mapName || resolveMapLabel(selectedMapName) }}</span>
          </div>
          <span>{{ selectedRoundEvents.length || 0 }} 回合</span>
        </div>
        <div v-if="!roundEventMaps.length" class="empty-state">本场比赛暂无回合记录</div>
        <template v-else>
          <div class="match-round-picker">
            <button
              v-for="round in selectedRoundEvents"
              :key="`${selectedRoundEventMap?.mapIndex || 0}-${round.roundNumber}`"
              type="button"
              class="match-round-pick-btn"
              :class="{ active: normalizeMapIndex(activeRound?.roundNumber) === normalizeMapIndex(round.roundNumber), winner: !!roundWinnerSide(round) }"
              @click="selectRound(round)"
            >
              <span>回合 {{ round.roundNumber }}</span>
              <i class="match-round-winner-mark">
                <img v-if="roundWinnerLogo(round)" :src="roundWinnerLogo(round)" alt="" loading="lazy" @error="handleImageError" />
                <b v-else>{{ roundWinnerName(round).slice(0, 1) }}</b>
                <em v-if="roundWinnerSide(round)">胜</em>
              </i>
            </button>
          </div>

          <div class="match-round-focus">
            <div class="match-round-focus-head">
              <strong>R{{ activeRound?.roundNumber || '-' }}</strong>
              <span>{{ roundWinnerText(activeRound) }}</span>
            </div>
            <div class="match-round-timeline">
              <div class="match-round-team-labels">
                <div class="match-round-side-title">
                  <span class="match-round-side-logo">
                    <img v-if="teamALogo" :src="teamALogo" alt="" loading="lazy" @error="handleImageError" />
                    <b v-else>{{ String(detail.teamA?.name || '-').slice(0, 1) }}</b>
                  </span>
                  <span v-if="activeRoundTeamSide('teamA')" class="match-round-side-badge" :class="`side-${activeRoundTeamSide('teamA').toLowerCase()}`">
                    {{ activeRoundTeamSide('teamA') }}
                  </span>
                  <strong>{{ detail.teamA?.name || 'Team A' }}</strong>
                </div>
                <div class="match-round-side-title right">
                  <strong>{{ detail.teamB?.name || 'Team B' }}</strong>
                  <span v-if="activeRoundTeamSide('teamB')" class="match-round-side-badge" :class="`side-${activeRoundTeamSide('teamB').toLowerCase()}`">
                    {{ activeRoundTeamSide('teamB') }}
                  </span>
                  <span class="match-round-side-logo">
                    <img v-if="teamBLogo" :src="teamBLogo" alt="" loading="lazy" @error="handleImageError" />
                    <b v-else>{{ String(detail.teamB?.name || '-').slice(0, 1) }}</b>
                  </span>
                </div>
              </div>

              <div class="match-round-divider" aria-hidden="true">
                <i></i>
              </div>

              <div class="match-round-ordered-list">
                <p
                  v-for="(event, idx) in orderedRoundEvents"
                  :key="`${activeRound?.roundNumber || 'round'}-${event.updateVersion || idx}-${idx}`"
                  class="match-round-event-row"
                  :class="event.timelineSide === 'teamB' ? 'side-b' : 'side-a'"
                >
                  <span class="match-round-event-card" :class="`et-${event.eventType || 'unknown'}`" v-html="eventDisplayHtml(event)"></span>
                </p>
                <div v-if="!orderedRoundEvents.length" class="match-round-empty-side">本回合暂无事件</div>
              </div>
            </div>
          </div>
        </template>
      </article>
    </div>
  </section>
</template>
