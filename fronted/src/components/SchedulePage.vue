<script setup>
import { computed } from 'vue'

const props = defineProps({
  viewMode: { type: String, default: 'fixture' },
  dateFilter: { type: String, default: '' },
  dateMin: { type: String, default: '' },
  dateMax: { type: String, default: '' },
  tierFilter: { type: String, default: 'b_or_above' },
  tierOptions: { type: Array, default: () => [] },
  rows: { type: Array, default: () => [] },
  hasMore: { type: Boolean, default: false },
  loadingMore: { type: Boolean, default: false },
  loading: { type: Boolean, default: false },
  error: { type: String, default: '' },
  resolveMatchDateText: { type: Function, required: true },
  resolveMatchTeamLogo: { type: Function, required: true },
  resolveScheduleScorePart: { type: Function, required: true },
  isResultWinner: { type: Function, required: true },
  isResultLoser: { type: Function, required: true },
  resolveMatchKickoffTime: { type: Function, required: true },
  resolveScheduleStatusText: { type: Function, required: true },
  resolveRowTierText: { type: Function, default: () => '' },
  resolveSchedulePrediction: { type: Function, default: () => ({ available: false, label: '预测待定', reason: '数据不足' }) },
  isTbdTeamName: { type: Function, default: () => false },
  imageErrorHandler: { type: Function, default: null },
})

const emit = defineEmits([
  'update:viewMode',
  'update:dateFilter',
  'update:tierFilter',
  'open-match',
  'load-more',
])

const updateViewMode = (value) => emit('update:viewMode', value)
const updateDateFilter = (event) => emit('update:dateFilter', event?.target?.value || '')
const updateTierFilter = (event) => emit('update:tierFilter', event?.target?.value || 'b_or_above')
const clearDate = () => emit('update:dateFilter', '')
const visibleRows = computed(() => props.rows || [])

const groupedVisibleRows = computed(() => {
  const groups = []
  let current = null

  for (const row of visibleRows.value) {
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

const loadMoreRows = () => {
  if (!props.hasMore || props.loading || props.loadingMore) return
  emit('load-more')
}

const handleImageError = (event) => {
  if (typeof props.imageErrorHandler === 'function') props.imageErrorHandler(event)
}

const handleScheduleScroll = (event) => {
  const container = event.currentTarget
  if (!(container instanceof HTMLElement)) return
  const nearBottom = container.scrollTop + container.clientHeight >= container.scrollHeight - 24
  if (nearBottom) {
    loadMoreRows()
  }
}

const scheduleTierLabel = (row) => String(props.resolveRowTierText(row) || '-').trim() || '-'
const scheduleCardTone = (row) => {
  const status = props.resolveScheduleStatusText(row)
  if (status === '进行中') return 'live'
  if (props.viewMode === 'result') return 'result'
  return 'fixture'
}
const predictionBarStyle = (prediction) => ({ '--prediction-a': `${prediction?.teamAProbability || 50}%` })
</script>

<template>
  <section class="section-card">
    <div class="section-topline">
      <h2>比赛赛程</h2>
      <div class="section-topline-right schedule-topline-right">
        <div class="schedule-view-toggle">
          <button
            type="button"
            class="schedule-view-btn"
            :class="{ active: viewMode === 'fixture' }"
            @click="updateViewMode('fixture')"
          >
            赛程
          </button>
          <button
            type="button"
            class="schedule-view-btn"
            :class="{ active: viewMode === 'result' }"
            @click="updateViewMode('result')"
          >
            赛果
          </button>
        </div>
        <label class="schedule-date-filter">
          <span>日期</span>
          <input
            :value="dateFilter"
            type="date"
            :min="dateMin"
            :max="dateMax"
            @input="updateDateFilter"
          />
        </label>
        <label class="schedule-date-filter">
          <span>级别</span>
          <select :value="tierFilter" @change="updateTierFilter">
            <option
              v-for="item in tierOptions"
              :key="item.value"
              :value="item.value"
            >
              {{ item.label }}
            </option>
          </select>
        </label>
        <button
          v-if="dateFilter"
          type="button"
          class="schedule-date-clear"
          @click="clearDate"
        >
          清空
        </button>
        <span class="section-count">{{ rows.length }} 条</span>
      </div>
    </div>

    <div v-if="error" class="empty-state">{{ error }}</div>
    <div v-else class="table-wrap schedule-scroll-wrap" @scroll="handleScheduleScroll">
      <div v-if="loading && !groupedVisibleRows.length" class="empty-state">正在从数据库加载筛选结果...</div>
      <div v-else-if="!groupedVisibleRows.length" class="empty-state">暂无匹配数据</div>
      <template v-else>
        <div
          v-for="group in groupedVisibleRows"
          :key="group.date"
          class="schedule-day-block"
        >
          <div class="schedule-day-title">{{ group.date }} · {{ group.rows.length }} 场比赛</div>
          <div class="schedule-match-list">
            <button
              v-for="row in group.rows"
              :key="row.matchId || `${resolveMatchDateText(row)}-${row.tournament}-${row.teamA}-${row.teamB}-${row.matchTime || ''}`"
              type="button"
              class="schedule-match-card"
              :class="[
                `tone-${scheduleCardTone(row)}`,
                {
                  'schedule-match-card--weak': isTbdTeamName(row.teamA) || isTbdTeamName(row.teamB),
                  'schedule-match-card--high': ['S', 'S+', 'MAJOR'].some((tier) => scheduleTierLabel(row).toUpperCase().includes(tier)),
                },
              ]"
              @click="$emit('open-match', row)"
            >
              <div class="schedule-card-meta">
                <span class="schedule-tier-badge">{{ scheduleTierLabel(row) }}</span>
                <strong>{{ row.tournament || '-' }}</strong>
                <span class="schedule-stage-chip">{{ row.stage || '-' }}</span>
                <span class="schedule-time-chip">{{ resolveMatchKickoffTime(row) }}</span>
                <span class="schedule-status-chip" :class="`tone-${scheduleCardTone(row)}`">{{ resolveScheduleStatusText(row) }}</span>
              </div>

              <div class="schedule-card-matchup">
                <span
                  class="schedule-team-panel"
                  :class="{
                    'schedule-team-panel--tbd': isTbdTeamName(row.teamA),
                    'schedule-side-winner': isResultWinner(row, 'A'),
                    'schedule-side-loser': isResultLoser(row, 'A'),
                  }"
                >
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

                <span
                  class="schedule-team-panel schedule-team-panel-b"
                  :class="{
                    'schedule-team-panel--tbd': isTbdTeamName(row.teamB),
                    'schedule-side-winner': isResultWinner(row, 'B'),
                    'schedule-side-loser': isResultLoser(row, 'B'),
                  }"
                >
                  <span v-if="resolveMatchTeamLogo(row, 'B')" class="schedule-team-logo-wrap">
                    <img class="schedule-team-logo" :src="resolveMatchTeamLogo(row, 'B')" alt="" loading="lazy" @error="handleImageError" />
                  </span>
                  <b v-else class="schedule-team-fallback">{{ String(row.teamB || '-').slice(0, 1) }}</b>
                  <strong>{{ row.teamB || '-' }}</strong>
                </span>
              </div>

              <div v-if="resolveSchedulePrediction(row).available" class="schedule-prediction" :style="predictionBarStyle(resolveSchedulePrediction(row))">
                <div class="schedule-prediction-topline">
                  <span>预测胜率</span>
                  <b>{{ resolveSchedulePrediction(row).teamAProbability }}% · {{ resolveSchedulePrediction(row).teamBProbability }}%</b>
                  <small>置信度 {{ resolveSchedulePrediction(row).confidence }}</small>
                </div>
                <div class="schedule-prediction-bar"><i></i></div>
                <div class="schedule-prediction-teams">
                  <span>{{ row.teamA || '-' }}</span>
                  <span>{{ row.teamB || '-' }}</span>
                </div>
              </div>
              <div v-else class="schedule-prediction schedule-prediction-muted">
                <span>{{ resolveSchedulePrediction(row).label }}</span>
                <small>{{ resolveSchedulePrediction(row).reason }}</small>
              </div>
            </button>
          </div>
        </div>
      </template>
    </div>
    <div v-if="loadingMore" class="empty-state schedule-load-tip">正在加载更多赛程...</div>
    <div v-else-if="hasMore" class="empty-state schedule-load-tip">向下滚动加载下一批 20 条</div>
  </section>
</template>
