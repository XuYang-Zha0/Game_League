<script setup>
import { computed, ref } from 'vue'

const props = defineProps({
  tournament: { type: Object, default: null },
  teams: { type: Array, default: () => [] },
  matches: { type: Array, default: () => [] },
  summaryCards: { type: Array, default: () => [] },
  resolveMatchDateText: { type: Function, required: true },
  resolveMatchTeamLogo: { type: Function, required: true },
  resolveScheduleScorePart: { type: Function, required: true },
  isResultWinner: { type: Function, required: true },
  isResultLoser: { type: Function, required: true },
  resolveMatchKickoffTime: { type: Function, required: true },
  resolveScheduleStatusText: { type: Function, required: true },
  imageErrorHandler: { type: Function, default: null },
})

const emit = defineEmits(['back', 'open-match', 'open-team'])

const displayName = computed(() => props.tournament?.name || props.matches?.[0]?.tournament || '赛事详情')
const tierText = computed(() => props.tournament?.tier || props.tournament?.grade || '-')
const regionText = computed(() => props.tournament?.region || '-')
const prizeText = computed(() => props.tournament?.prize || props.tournament?.prizePool || '-')
const dateRangeText = computed(() => {
  const start = props.tournament?.start || '-'
  const end = props.tournament?.end || ''
  return end ? `${start} → ${end}` : start
})
const statusText = computed(() => props.tournament?.status || '-')
const bilibiliLive = computed(() => props.tournament?.bilibiliLive || null)
const liveRooms = computed(() => (Array.isArray(bilibiliLive.value?.rooms) ? bilibiliLive.value.rooms : []).filter((room) => room?.roomId))
const activeLiveRoomKey = ref('')
const activeLiveRoom = computed(() => {
  const rooms = liveRooms.value
  if (!rooms.length) return bilibiliLive.value || null
  const key = String(activeLiveRoomKey.value || '').trim()
  return rooms.find((room) => String(room?.key || room?.roomId || '').trim() === key) || rooms[0]
})
const isLiveSupported = computed(() => Boolean(bilibiliLive.value?.supported))
const isLiveBound = computed(() => Boolean(bilibiliLive.value?.hasRoom))
const isStreamingNow = computed(() => String(activeLiveRoom.value?.status || bilibiliLive.value?.status || '').trim().toLowerCase() === 'live')
const liveStatusText = computed(() => {
  if (!isLiveSupported.value) return ''
  if (isStreamingNow.value) return '正在直播'
  if (isLiveBound.value) return '有直播'
  return '直播待接入'
})
const selectLiveRoom = (room) => {
  const key = String(room?.key || room?.roomId || '').trim()
  if (key) activeLiveRoomKey.value = key
}
const openLivePopup = () => {
  const url = String(activeLiveRoom.value?.url || '').trim()
  if (!url || typeof window === 'undefined') return
  window.open(url, `bilibili-live-${activeLiveRoom.value?.roomId || 'room'}`, 'popup=yes,width=1440,height=900,resizable=yes,scrollbars=yes')
}
const openLiveTab = () => {
  const url = String(activeLiveRoom.value?.url || '').trim()
  if (!url || typeof window === 'undefined') return
  window.open(url, '_blank', 'noopener,noreferrer')
}
const liveRoomRoleText = (room) => {
  const key = String(room?.key || '').trim()
  if (key === 'main_stage') return '主舞台'
  if (key === 'service_desk') return '服务台'
  if (key === 'cs_advent') return '二路流'
  if (key === 'deyun_lianggui') return '社区流'
  return '直播间'
}
const liveControlSummary = computed(() => `${liveRooms.value.length || 0} 路直播源，可切换后以小窗或新标签打开。`)
const tournamentMatchViewMode = ref('fixture')
const tournamentFixtureMatches = computed(() => (props.matches || []).filter((row) => props.resolveScheduleStatusText(row) !== '已完赛'))
const tournamentResultMatches = computed(() => (props.matches || []).filter((row) => props.resolveScheduleStatusText(row) === '已完赛'))
const visibleMatches = computed(() => (tournamentMatchViewMode.value === 'result' ? tournamentResultMatches.value : tournamentFixtureMatches.value))
const visibleMatchCountText = computed(() => `${visibleMatches.value.length} / ${props.matches.length} 场`)

const groupedMatches = computed(() => {
  const groups = []
  let current = null

  for (const row of visibleMatches.value) {
    const dateText = String(props.resolveMatchDateText(row) || '').trim()
    const key = /^\d{4}-\d{2}-\d{2}$/.test(dateText) ? dateText : '未标注日期'
    if (!current || current.date !== key) {
      current = { date: key, rows: [] }
      groups.push(current)
    }
    current.rows.push(row)
  }

  return groups
})

const cardTone = (row) => {
  const status = props.resolveScheduleStatusText(row)
  if (status === '进行中') return 'live'
  if (status === '已完赛') return 'result'
  return 'fixture'
}

const handleImageError = (event) => {
  if (typeof props.imageErrorHandler === 'function') props.imageErrorHandler(event)
}

const openTeam = (team) => {
  if (!team?.teamKey && !team?.name) return
  emit('open-team', team)
}
</script>

<template>
  <section class="section-card tournament-detail-stack">
    <div class="section-topline">
      <h2>赛事详情</h2>
      <button class="outline-btn" type="button" @click="emit('back')">返回赛事列表</button>
    </div>

    <article class="tournament-hero-card">
      <div class="tournament-hero-main">
        <p class="section-eyebrow">赛事情报档案</p>
        <div class="tournament-title-row">
          <h3>{{ displayName }}</h3>
          <span v-if="isLiveSupported" class="bilibili-live-badge" :class="{ live: isStreamingNow, ready: isLiveBound && !isStreamingNow }">
            <i v-if="isStreamingNow" class="bilibili-live-dot"></i>
            {{ liveStatusText }}
          </span>
        </div>
        <div class="tournament-meta-row">
          <span>{{ tierText }}</span>
          <span>{{ regionText }}</span>
          <span>{{ statusText }}</span>
        </div>
      </div>
      <div class="tournament-meta-grid">
        <p><span>比赛时间</span><b>{{ dateRangeText }}</b></p>
        <p><span>奖金池</span><b>{{ prizeText }}</b></p>
        <p><span>比赛数</span><b>{{ matches.length }}</b></p>
      </div>
    </article>

    <div class="tournament-summary-grid">
      <article v-for="item in summaryCards" :key="item.key" class="summary-stat-card">
        <span>{{ item.label }}</span>
        <b>{{ item.value }}</b>
      </article>
    </div>

    <article v-if="isLiveSupported" class="detail-card tournament-live-card">
      <div class="panel-title-row tournament-live-title-row">
        <h3>B 站直播室</h3>
        <span class="tournament-live-status" :class="{ live: isStreamingNow, ready: isLiveBound && !isStreamingNow }">
          <i v-if="isStreamingNow" class="bilibili-live-dot"></i>
          {{ liveStatusText }}
        </span>
      </div>
      <div v-if="isLiveBound" class="tournament-live-panel">
        <div class="tournament-live-console-head">
          <strong>直播控制台</strong>
          <small>{{ liveControlSummary }}</small>
        </div>
        <div v-if="liveRooms.length > 1" class="tournament-live-room-switcher">
          <button
            v-for="room in liveRooms"
            :key="room.key || room.roomId"
            type="button"
            class="tournament-live-room-btn"
            :class="{ active: (activeLiveRoom?.key || activeLiveRoom?.roomId) === (room.key || room.roomId) }"
            @click="selectLiveRoom(room)"
          >
            <span>{{ room.title || room.ownerName || `直播间 ${room.roomId}` }}</span>
            <small>{{ liveRoomRoleText(room) }}</small>
          </button>
        </div>
        <div class="tournament-live-console-grid">
          <div class="tournament-live-copy">
            <strong>{{ activeLiveRoom?.title || bilibiliLive.title || displayName }}</strong>
            <small>
              {{ liveRoomRoleText(activeLiveRoom) }}
              <template v-if="activeLiveRoom?.ownerName"> · {{ activeLiveRoom.ownerName }}</template>
              <template v-if="activeLiveRoom?.roomId"> · 房间号 {{ activeLiveRoom.roomId }}</template>
            </small>
          </div>
          <div class="tournament-live-action-row">
            <button type="button" class="outline-btn tournament-live-action-btn" @click="openLivePopup">小窗打开</button>
            <button type="button" class="outline-btn tournament-live-action-btn" @click="openLiveTab">新标签打开</button>
          </div>
        </div>
      </div>
      <div v-else class="empty-state">该赛事已支持直播标识，当前尚未配置直播间。</div>
    </article>

    <article class="detail-card tournament-team-section">
      <div class="panel-title-row">
        <h3>参赛战队</h3>
        <span>{{ teams.length }} 支</span>
      </div>
      <div v-if="!teams.length" class="empty-state">暂无已确定参赛战队</div>
      <div v-else class="tournament-team-grid">
        <button
          v-for="team in teams"
          :key="team.teamKey || team.name"
          type="button"
          class="tournament-team-card"
          @click="openTeam(team)"
        >
          <span class="tournament-team-logo-wrap">
            <img v-if="team.logo" :src="team.logo" alt="" loading="lazy" @error="handleImageError" />
            <b v-else>{{ String(team.name || '-').slice(0, 1) }}</b>
          </span>
          <span class="tournament-team-info">
            <strong>{{ team.name || '-' }}</strong>
            <small>{{ team.region || '-' }}</small>
          </span>
          <span class="tournament-team-record">
            <b>{{ team.wins }}-{{ team.losses }}</b>
            <small>{{ team.matches }} 场</small>
          </span>
        </button>
      </div>
    </article>

    <article class="detail-card tournament-match-section">
      <div class="panel-title-row tournament-match-title-row">
        <h3>全部比赛</h3>
        <div class="tournament-match-controls">
          <div class="schedule-view-toggle">
            <button
              type="button"
              class="schedule-view-btn"
              :class="{ active: tournamentMatchViewMode === 'fixture' }"
              @click="tournamentMatchViewMode = 'fixture'"
            >
              赛程
            </button>
            <button
              type="button"
              class="schedule-view-btn"
              :class="{ active: tournamentMatchViewMode === 'result' }"
              @click="tournamentMatchViewMode = 'result'"
            >
              赛果
            </button>
          </div>
          <span>{{ visibleMatchCountText }}</span>
        </div>
      </div>
      <div v-if="!matches.length" class="empty-state">暂无该赛事比赛数据</div>
      <div v-else-if="!groupedMatches.length" class="empty-state">暂无{{ tournamentMatchViewMode === 'result' ? '赛果' : '赛程' }}数据</div>
      <template v-else>
        <div v-for="group in groupedMatches" :key="group.date" class="schedule-day-block tournament-match-day">
          <div class="schedule-day-title">{{ group.date }} · {{ group.rows.length }} 场比赛</div>
          <div class="schedule-match-list tournament-match-list">
            <button
              v-for="row in group.rows"
              :key="row.matchId || `${resolveMatchDateText(row)}-${row.tournament}-${row.teamA}-${row.teamB}-${row.matchTime || ''}`"
              type="button"
              class="schedule-match-card"
              :class="`tone-${cardTone(row)}`"
              @click="emit('open-match', row)"
            >
              <div class="schedule-card-meta tournament-match-meta">
                <span class="schedule-stage-chip">{{ row.stage || '-' }}</span>
                <strong>{{ row.tournament || displayName }}</strong>
                <span class="schedule-time-chip">{{ resolveMatchKickoffTime(row) }}</span>
                <span class="schedule-status-chip" :class="`tone-${cardTone(row)}`">{{ resolveScheduleStatusText(row) }}</span>
              </div>

              <div class="schedule-card-matchup">
                <span class="schedule-team-panel">
                  <span v-if="resolveMatchTeamLogo(row, 'A')" class="schedule-team-logo-wrap">
                    <img class="schedule-team-logo" :src="resolveMatchTeamLogo(row, 'A')" alt="" loading="lazy" @error="handleImageError" />
                  </span>
                  <b v-else class="schedule-team-fallback">{{ String(row.teamA || '-').slice(0, 1) }}</b>
                  <strong>{{ row.teamA || '-' }}</strong>
                </span>

                <span class="schedule-card-center">
                  <b>
                    <span :class="{ 'schedule-score-winner': isResultWinner(row, 'A'), 'schedule-score-loser': isResultLoser(row, 'A') }">{{ resolveScheduleScorePart(row, 'A') }}</span>
                    <i>:</i>
                    <span :class="{ 'schedule-score-winner': isResultWinner(row, 'B'), 'schedule-score-loser': isResultLoser(row, 'B') }">{{ resolveScheduleScorePart(row, 'B') }}</span>
                  </b>
                  <small>{{ resolveScheduleStatusText(row) === '未开赛' ? 'VS' : resolveMatchKickoffTime(row) }}</small>
                </span>

                <span class="schedule-team-panel schedule-team-panel-b">
                  <span v-if="resolveMatchTeamLogo(row, 'B')" class="schedule-team-logo-wrap">
                    <img class="schedule-team-logo" :src="resolveMatchTeamLogo(row, 'B')" alt="" loading="lazy" @error="handleImageError" />
                  </span>
                  <b v-else class="schedule-team-fallback">{{ String(row.teamB || '-').slice(0, 1) }}</b>
                  <strong>{{ row.teamB || '-' }}</strong>
                </span>
              </div>
            </button>
          </div>
        </div>
      </template>
    </article>
  </section>
</template>
