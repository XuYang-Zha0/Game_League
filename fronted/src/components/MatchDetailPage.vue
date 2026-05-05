<script setup>
import { computed, ref, watch } from 'vue'

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

const mapRows = computed(() => (Array.isArray(props.detail?.maps) ? props.detail.maps : []))
const mapPlayerStats = computed(() =>
  Array.isArray(props.detail?.mapPlayerStats) ? props.detail.mapPlayerStats : [],
)

const selectedMapIndex = ref(0)

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
const selectedTeamAPlayerKey = ref('')
const selectedTeamBPlayerKey = ref('')
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

const toMetricNumber = (value) => {
  const text = String(value ?? '').replace('%', '').trim()
  const num = Number.parseFloat(text)
  return Number.isFinite(num) ? num : Number.NEGATIVE_INFINITY
}

const playerKey = (player, idx = 0) => {
  const pid = String(player?.playerId ?? '').trim()
  const name = String(player?.name ?? '').trim()
  return `${pid || name || 'player'}::${idx}`
}

const pickTopRatedPlayerKey = (players) => {
  if (!Array.isArray(players) || !players.length) return ''
  let bestIdx = 0
  let bestRating = Number.NEGATIVE_INFINITY
  for (let i = 0; i < players.length; i += 1) {
    const current = toMetricNumber(players[i]?.rating)
    if (current > bestRating) {
      bestRating = current
      bestIdx = i
    }
  }
  return playerKey(players[bestIdx], bestIdx)
}

const ensureSelectedPlayerKey = (currentKey, players) => {
  if (!Array.isArray(players) || !players.length) return ''
  if (currentKey) {
    const exists = players.some((player, idx) => playerKey(player, idx) === currentKey)
    if (exists) return currentKey
  }
  return pickTopRatedPlayerKey(players)
}

watch(
  [teamAPlayers, teamBPlayers],
  ([aPlayers, bPlayers]) => {
    selectedTeamAPlayerKey.value = ensureSelectedPlayerKey(selectedTeamAPlayerKey.value, aPlayers)
    selectedTeamBPlayerKey.value = ensureSelectedPlayerKey(selectedTeamBPlayerKey.value, bPlayers)
  },
  { immediate: true },
)

const resolveActivePlayer = (players, key) => {
  if (!Array.isArray(players) || !players.length) return null
  if (!key) return players[0]
  return players.find((player, idx) => playerKey(player, idx) === key) || players[0]
}

const activeTeamAPlayer = computed(() =>
  resolveActivePlayer(teamAPlayers.value, selectedTeamAPlayerKey.value),
)
const activeTeamBPlayer = computed(() =>
  resolveActivePlayer(teamBPlayers.value, selectedTeamBPlayerKey.value),
)
const isLolGame = computed(() => props.gameId === 'lol')

const selectActivePlayer = (teamSide, key) => {
  if (teamSide === 'teamA') {
    selectedTeamAPlayerKey.value = key
    return
  }
  selectedTeamBPlayerKey.value = key
}

const playerAvatarSrc = (player) => {
  const avatar = String(player?.avatar ?? '').trim()
  if (usableImage(avatar)) return avatar
  return ''
}

const playerInitial = (player) => {
  const name = String(player?.name ?? '').trim()
  return name ? name.slice(0, 1).toUpperCase() : '?'
}

const playerStatsItems = (player) => {
  if (isLolGame.value) {
    return [
      { label: '英雄', value: valueOrDash(player?.champion) },
      { label: 'KDA', value: valueOrDash(player?.kda || player?.rating) },
      { label: 'K/D/A', value: kdaText(player) },
      { label: '补刀', value: valueOrDash(player?.cs) },
    ]
  }

  return [
    { label: 'Rating', value: valueOrDash(player?.rating) },
    { label: 'ADR', value: valueOrDash(player?.adr) },
    { label: 'KAST', value: valueOrDash(player?.kast) },
    { label: 'KPR', value: valueOrDash(player?.kpr) },
    { label: 'KD', value: valueOrDash(player?.kd) },
    { label: 'K/D/A', value: kdaText(player) },
  ]
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

const valueOrDash = (value) => {
  const text = String(value ?? '').trim()
  return text || '-'
}
</script>

<template>
  <section class="section-card">
    <div class="section-topline">
      <h2>比赛详情</h2>
      <button type="button" class="match-detail-back-btn" @click="$emit('back')">返回赛程</button>
    </div>

    <div v-if="state === 'loading'" class="empty-state">正在加载比赛详情...</div>
    <div v-else-if="state === 'error'" class="empty-state">{{ error || '比赛详情加载失败' }}</div>
    <div v-else-if="!detail || detail.exists === false" class="empty-state">未找到该比赛的数据库记录</div>
    <div v-else class="match-detail-wrap">
      <div class="match-detail-meta">
        <p><span>开赛时间</span><b>{{ detail.matchTime || detail.date || '-' }}</b></p>
        <p><span>赛事</span><b>{{ detail.tournament?.name || '-' }}</b></p>
        <p><span>级别</span><b>{{ detail.tournament?.tier || '-' }}</b></p>
        <p><span>状态</span><b>{{ detail.statusText || '-' }}</b></p>
        <p><span>BO</span><b>{{ detail.bo ? `BO${detail.bo}` : '-' }}</b></p>
      </div>

      <div class="match-detail-scoreboard">
        <div class="match-detail-team team-a">
          <img v-if="teamALogo" :src="teamALogo" alt="" loading="lazy" @error="handleImageError" />
          <span>{{ detail.teamA?.name || '-' }}</span>
        </div>
        <div class="match-detail-score">
          <b>{{ teamScore(detail.teamA) }}</b>
          <span>:</span>
          <b>{{ teamScore(detail.teamB) }}</b>
        </div>
        <div class="match-detail-team team-b">
          <span>{{ detail.teamB?.name || '-' }}</span>
          <img v-if="teamBLogo" :src="teamBLogo" alt="" loading="lazy" @error="handleImageError" />
        </div>
      </div>

      <div class="match-detail-note">
        <p><span>胜者</span><b>{{ detail.winner || '-' }}</b></p>
        <p><span>总比分</span><b>{{ detail.score || '-' }}</b></p>
      </div>

      <div class="table-wrap">
        <div class="table-head mini-head milestone-grid">
          <span>图序</span><span>地图</span><span>比分</span><span>胜者</span>
        </div>
        <div v-if="!hasMaps(detail)" class="empty-state">暂无分图数据</div>
        <div
          v-for="row in mapRows"
          :key="`${row.index}-${row.map}`"
          :class="[
            'table-row',
            'milestone-grid',
            'match-map-row-clickable',
            { 'match-map-row-active': normalizeMapIndex(row.index) === normalizeMapIndex(selectedMapIndex) },
          ]"
          @click="onSelectMap(row)"
        >
          <span>{{ row.index || '-' }}</span>
          <span class="map-name-badge" :style="mapBadgeStyle(row.map)">{{ resolveMapLabel(row.map) }}</span>
          <span>{{ scoreText(row) }}</span>
          <span>{{ winnerText(row, detail) }}</span>
        </div>
      </div>

      <section class="match-player-panel">
        <div class="section-topline match-player-headline">
          <h3>选手数据</h3>
          <span class="map-name-cell">
            <span>当前地图：</span>
            <span class="map-name-badge map-name-badge-compact" :style="mapBadgeStyle(selectedMapName)">
              {{ resolveMapLabel(selectedMapName) }}
            </span>
          </span>
        </div>

        <div v-if="!hasAnyPlayerStats" class="empty-state">暂无选手数据</div>
        <div v-else class="match-player-cards">
          <article class="player-focus-card team-a-card">
            <aside class="player-roster player-roster-left">
              <button
                v-for="(row, idx) in teamAPlayers"
                :key="`a-pill-${row.playerId || row.name || idx}`"
                type="button"
                :class="[
                  'player-pill',
                  { 'player-pill-active': playerKey(row, idx) === selectedTeamAPlayerKey },
                ]"
                @click="selectActivePlayer('teamA', playerKey(row, idx))"
              >
                <span class="player-pill-name">{{ row.name || '-' }}</span>
              </button>
            </aside>

            <div class="player-focus-center">
              <div class="player-avatar-shell">
                <img
                  v-if="playerAvatarSrc(activeTeamAPlayer)"
                  :src="playerAvatarSrc(activeTeamAPlayer)"
                  alt=""
                  loading="lazy"
                  referrerpolicy="no-referrer"
                  @error="handleImageError"
                />
                <span v-else class="player-focus-fallback">{{ playerInitial(activeTeamAPlayer) }}</span>
              </div>
              <p class="player-focus-name">{{ activeTeamAPlayer?.name || '-' }}</p>
            </div>

            <div class="player-focus-stats">
              <p v-for="item in playerStatsItems(activeTeamAPlayer)" :key="`a-stat-${item.label}`">
                <span>{{ item.label }}</span>
                <b>{{ item.value }}</b>
              </p>
            </div>
          </article>

          <article class="player-focus-card team-b-card">
            <div class="player-focus-stats">
              <p v-for="item in playerStatsItems(activeTeamBPlayer)" :key="`b-stat-${item.label}`">
                <span>{{ item.label }}</span>
                <b>{{ item.value }}</b>
              </p>
            </div>

            <div class="player-focus-center">
              <div class="player-avatar-shell">
                <img
                  v-if="playerAvatarSrc(activeTeamBPlayer)"
                  :src="playerAvatarSrc(activeTeamBPlayer)"
                  alt=""
                  loading="lazy"
                  referrerpolicy="no-referrer"
                  @error="handleImageError"
                />
                <span v-else class="player-focus-fallback">{{ playerInitial(activeTeamBPlayer) }}</span>
              </div>
              <p class="player-focus-name">{{ activeTeamBPlayer?.name || '-' }}</p>
            </div>

            <aside class="player-roster player-roster-right">
              <button
                v-for="(row, idx) in teamBPlayers"
                :key="`b-pill-${row.playerId || row.name || idx}`"
                type="button"
                :class="[
                  'player-pill',
                  { 'player-pill-active': playerKey(row, idx) === selectedTeamBPlayerKey },
                ]"
                @click="selectActivePlayer('teamB', playerKey(row, idx))"
              >
                <span class="player-pill-name">{{ row.name || '-' }}</span>
              </button>
            </aside>
          </article>
        </div>
      </section>
    </div>
  </section>
</template>


