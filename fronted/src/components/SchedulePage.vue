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
      <div class="table-head schedule-grid schedule-head-sticky">
        <span>日期</span>
        <span>赛事</span>
        <span class="schedule-head-matchup">
          <span class="schedule-head-side" aria-hidden="true"></span>
          <span class="schedule-head-score">对阵</span>
          <span class="schedule-head-side" aria-hidden="true"></span>
        </span>
        <span>时间</span>
        <span class="schedule-stage-head">赛段</span>
        <span>状态</span>
      </div>
      <div v-if="loading && !groupedVisibleRows.length" class="empty-state">正在从数据库加载筛选结果...</div>
      <div v-else-if="!groupedVisibleRows.length" class="empty-state">暂无匹配数据</div>
      <template v-else>
        <div
          v-for="group in groupedVisibleRows"
          :key="group.date"
          class="schedule-day-block"
        >
          <div class="schedule-day-title">{{ group.date }}</div>
          <div
            v-for="row in group.rows"
            :key="row.matchId || `${resolveMatchDateText(row)}-${row.tournament}-${row.teamA}-${row.teamB}-${row.matchTime || ''}`"
            class="table-row schedule-grid schedule-clickable-row"
            @click="$emit('open-match', row)"
          >
            <span>{{ resolveMatchDateText(row) }}</span>
            <span>{{ row.tournament || '-' }}</span>
            <span class="matchup-cell schedule-matchup-cell">
              <span
                class="team-with-logo schedule-team-side-a"
                :class="{
                  'schedule-side-winner': isResultWinner(row, 'A'),
                  'schedule-side-loser': isResultLoser(row, 'A'),
                }"
              >
                <span v-if="resolveMatchTeamLogo(row, 'A')" class="team-logo-badge-wrap">
                  <img
                    class="team-logo-badge"
                    :src="resolveMatchTeamLogo(row, 'A')"
                    alt=""
                    loading="lazy"
                    @error="handleImageError"
                  />
                </span>
                <span class="team-name-text">{{ row.teamA || '-' }}</span>
              </span>
              <span class="schedule-score-text">
                <span
                  :class="{
                    'schedule-score-winner': isResultWinner(row, 'A'),
                    'schedule-score-loser': isResultLoser(row, 'A'),
                  }"
                >
                  {{ resolveScheduleScorePart(row, 'A') }}
                </span>
                <span class="schedule-score-sep">:</span>
                <span
                  :class="{
                    'schedule-score-winner': isResultWinner(row, 'B'),
                    'schedule-score-loser': isResultLoser(row, 'B'),
                  }"
                >
                  {{ resolveScheduleScorePart(row, 'B') }}
                </span>
              </span>
              <span
                class="team-with-logo schedule-team-side-b"
                :class="{
                  'schedule-side-winner': isResultWinner(row, 'B'),
                  'schedule-side-loser': isResultLoser(row, 'B'),
                }"
              >
                <span class="team-name-text">{{ row.teamB || '-' }}</span>
                <span v-if="resolveMatchTeamLogo(row, 'B')" class="team-logo-badge-wrap">
                  <img
                    class="team-logo-badge"
                    :src="resolveMatchTeamLogo(row, 'B')"
                    alt=""
                    loading="lazy"
                    @error="handleImageError"
                  />
                </span>
              </span>
            </span>
            <span>{{ resolveMatchKickoffTime(row) }}</span>
            <span class="schedule-stage-cell" :title="row.stage || '-'">
              <span class="schedule-stage-text">{{ row.stage || '-' }}</span>
            </span>
            <span>{{ resolveScheduleStatusText(row) }}</span>
          </div>
        </div>
      </template>
    </div>
    <div v-if="loadingMore" class="empty-state schedule-load-tip">正在加载更多赛程...</div>
    <div v-else-if="hasMore" class="empty-state schedule-load-tip">向下滚动加载下一批 20 条</div>
  </section>
</template>
