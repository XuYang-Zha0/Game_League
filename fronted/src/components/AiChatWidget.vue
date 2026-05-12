<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { fetchAiChatStream } from '../data/platformService'
import { apiFetch } from '../data/apiConfig'

const props = defineProps({
  gameId: { type: String, default: 'cs2' },
  gameName: { type: String, default: 'CS2' },
  page: { type: String, default: 'home' },
  contextData: { type: Object, default: () => ({}) },
})

const isOpen = ref(false)
const isConfigured = ref(false)
const inputText = ref('')
const isStreaming = ref(false)
const messages = ref([])
const chatBodyRef = ref(null)
const inputRef = ref(null)
const panelRef = ref(null)
let abortController = null

const FAB_SIZE = 52

// ── drag state ──
const panelPos = ref({ x: null, y: null })
let dragState = null
let fabDragState = null
let suppressFabClick = false

const clampPanelPos = (x, y, size = panelSize.value) => {
  if (typeof window === 'undefined') return { x, y }
  const maxX = Math.max(8, window.innerWidth - size.w - 8)
  const maxY = Math.max(8, window.innerHeight - size.h - 8)
  return {
    x: Math.max(8, Math.min(maxX, x)),
    y: Math.max(8, Math.min(maxY, y)),
  }
}

const ensurePanelPosition = async () => {
  if (panelPos.value.x != null) return
  await nextTick()
  const rect = panelRef.value?.getBoundingClientRect()
  const w = rect?.width || panelSize.value.w
  const h = rect?.height || panelSize.value.h
  panelPos.value = clampPanelPos(window.innerWidth - w - 24, window.innerHeight - h - 24, { w, h })
}

const clampFabPos = (x, y) => clampPanelPos(x, y, { w: FAB_SIZE, h: FAB_SIZE })

const fabPosFromPanel = () => {
  if (panelPos.value.x == null) return null
  return clampFabPos(
    panelPos.value.x + panelSize.value.w - FAB_SIZE,
    panelPos.value.y + panelSize.value.h - FAB_SIZE,
  )
}

const panelPosFromFab = (x, y) => clampPanelPos(
  x - panelSize.value.w + FAB_SIZE,
  y - panelSize.value.h + FAB_SIZE,
)

const startDrag = (e) => {
  if (e.target.closest('button, input, .ai-chat-resize')) return
  const rect = panelRef.value?.getBoundingClientRect()
  if (!rect) return
  dragState = { startX: e.clientX, startY: e.clientY, left: rect.left, top: rect.top }
  document.addEventListener('mousemove', onDrag)
  document.addEventListener('mouseup', stopDrag)
  e.preventDefault()
}

const onDrag = (e) => {
  if (!dragState) return
  const dx = e.clientX - dragState.startX
  const dy = e.clientY - dragState.startY
  panelPos.value = clampPanelPos(dragState.left + dx, dragState.top + dy)
}

const stopDrag = () => {
  dragState = null
  document.removeEventListener('mousemove', onDrag)
  document.removeEventListener('mouseup', stopDrag)
}

const startFabDrag = (e) => {
  if (e.button !== 0) return
  if (panelPos.value.x == null) {
    const initialFab = clampFabPos(window.innerWidth - FAB_SIZE - 24, window.innerHeight - FAB_SIZE - 24)
    panelPos.value = panelPosFromFab(initialFab.x, initialFab.y)
  }
  const fabPos = fabPosFromPanel()
  fabDragState = {
    startX: e.clientX,
    startY: e.clientY,
    left: fabPos?.x || window.innerWidth - FAB_SIZE - 24,
    top: fabPos?.y || window.innerHeight - FAB_SIZE - 24,
    moved: false,
  }
  document.addEventListener('mousemove', onFabDrag)
  document.addEventListener('mouseup', stopFabDrag)
}

const onFabDrag = (e) => {
  if (!fabDragState) return
  const dx = e.clientX - fabDragState.startX
  const dy = e.clientY - fabDragState.startY
  if (Math.abs(dx) + Math.abs(dy) > 4) fabDragState.moved = true
  const fabPos = clampFabPos(fabDragState.left + dx, fabDragState.top + dy)
  panelPos.value = panelPosFromFab(fabPos.x, fabPos.y)
}

const stopFabDrag = () => {
  suppressFabClick = !!fabDragState?.moved
  fabDragState = null
  document.removeEventListener('mousemove', onFabDrag)
  document.removeEventListener('mouseup', stopFabDrag)
  if (suppressFabClick) {
    window.setTimeout(() => {
      suppressFabClick = false
    }, 0)
  }
}

// ── resize state ──
const panelSize = ref({ w: 400, h: 540 })
let resizeState = null

const startResize = (e) => {
  e.preventDefault()
  e.stopPropagation()
  resizeState = { startX: e.clientX, startY: e.clientY, w: panelSize.value.w, h: panelSize.value.h }
  document.addEventListener('mousemove', onResize)
  document.addEventListener('mouseup', stopResize)
}

const onResize = (e) => {
  if (!resizeState) return
  const dw = e.clientX - resizeState.startX
  const dh = e.clientY - resizeState.startY
  const size = {
    w: Math.max(320, Math.min(900, resizeState.w + dw)),
    h: Math.max(360, Math.min(900, resizeState.h + dh)),
  }
  panelSize.value = size
  if (panelPos.value.x != null) {
    panelPos.value = clampPanelPos(panelPos.value.x, panelPos.value.y, size)
  }
}

const stopResize = () => {
  resizeState = null
  document.removeEventListener('mousemove', onResize)
  document.removeEventListener('mouseup', stopResize)
}

const panelStyle = computed(() => {
  const style = {
    position: 'fixed',
    width: panelSize.value.w + 'px',
    height: panelSize.value.h + 'px',
  }
  if (panelPos.value.x != null) {
    style.left = panelPos.value.x + 'px'
    style.top = panelPos.value.y + 'px'
    style.right = 'auto'
    style.bottom = 'auto'
  }
  return style
})

const fabStyle = computed(() => {
  const pos = fabPosFromPanel()
  if (!pos) return {}
  return {
    position: 'fixed',
    left: pos.x + 'px',
    top: pos.y + 'px',
    right: 'auto',
    bottom: 'auto',
  }
})

const gameLabel = computed(() => {
  const map = { cs2: 'CS2', valorant: '无畏契约', lol: '英雄联盟' }
  return map[props.gameId] || props.gameName || 'CS2'
})

const welcomeText = computed(() => {
  const map = {
    cs2: '你好！我是 CS2 赛事 AI 分析助手。你可以问我关于战队排名、选手数据、地图胜率、近期比赛等方面的问题。',
    valorant: '你好！我是无畏契约赛事 AI 分析助手。你可以问我关于 VCT 赛事、选手 ACS/KDA、战队赛区排名等方面的问题。',
    lol: '你好！我是英雄联盟赛事 AI 分析助手。你可以问我关于 LCK/LPL 赛事、选手数据、版本 meta、战队运营风格等方面的问题。',
  }
  return map[props.gameId] || map.cs2
})

const checkStatus = async () => {
  try {
    const res = await apiFetch('/api/ai/status')
    const json = await res.json()
    isConfigured.value = !!(json?.data?.configured)
  } catch {
    isConfigured.value = false
  }
}

const scrollToBottom = async () => {
  await nextTick()
  if (chatBodyRef.value) {
    chatBodyRef.value.scrollTop = chatBodyRef.value.scrollHeight
  }
}

const addMessage = (role, content) => {
  messages.value.push({
    role,
    content,
    time: new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }),
  })
}

const renderContent = (text) =>
  text.replace(/\*\*(.+?)\*\*/g, '<b>$1</b>').replace(/\n/g, '<br>')

const cancelStream = () => {
  if (abortController) {
    abortController.abort()
    abortController = null
  }
  isStreaming.value = false
}

const doStreamQuery = (question, history, ctx) => {
  cancelStream()
  isStreaming.value = true
  addMessage('user', question)
  const aiIdx = messages.value.length
  addMessage('assistant', '')
  scrollToBottom()

  abortController = fetchAiChatStream(
    {
      gameId: props.gameId,
      gameName: props.gameName,
      page: props.page,
      question,
      context: ctx || props.contextData,
      history,
    },
    (chunk) => {
      messages.value[aiIdx].content += chunk
      scrollToBottom()
    },
    () => {
      isStreaming.value = false
      abortController = null
      if (!messages.value[aiIdx].content) {
        messages.value[aiIdx].content = 'AI 返回了空回复，请再试一次。'
      }
    },
    (err) => {
      isStreaming.value = false
      abortController = null
      if (!messages.value[aiIdx].content) {
        messages.value[aiIdx].content = `请求失败：${err.message || '网络错误'}`
      }
    },
  )
}

const sendMessage = () => {
  const text = inputText.value.trim()
  if (!text || isStreaming.value) return
  inputText.value = ''

  const history = messages.value
    .filter((m) => m.content)
    .map((m) => ({ role: m.role, content: m.content }))
  doStreamQuery(text, history)
}

const stopStream = () => {
  cancelStream()
}

const handleKeydown = (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    if (isStreaming.value) {
      stopStream()
    } else {
      sendMessage()
    }
  }
}

const toggleChat = () => {
  if (!isOpen.value && suppressFabClick) return
  isOpen.value = !isOpen.value
  if (isOpen.value && messages.value.length === 0) {
    addMessage('assistant', welcomeText.value)
  }
  if (isOpen.value) {
    ensurePanelPosition()
    nextTick(() => inputRef.value?.focus())
  }
}

const autoAnalyze = async (question, contextOverride) => {
  if (!isConfigured.value) {
    await checkStatus()
    if (!isConfigured.value) return
  }
  if (!isOpen.value) {
    isOpen.value = true
    ensurePanelPosition()
  }
  cancelStream()
  messages.value = []
  doStreamQuery(question, [], contextOverride || props.contextData)
}

const clearChat = () => {
  cancelStream()
  messages.value = []
  addMessage('assistant', welcomeText.value)
}

defineExpose({ autoAnalyze })

onMounted(() => {
  checkStatus()
})

onBeforeUnmount(() => {
  cancelStream()
  stopDrag()
  stopFabDrag()
  stopResize()
})

watch(
  () => props.gameId,
  () => {
    cancelStream()
    messages.value = []
    if (isOpen.value) {
      addMessage('assistant', welcomeText.value)
    }
  },
)
</script>

<template>
  <div class="ai-chat-root" :class="{ open: isOpen }">
    <button
      v-if="!isOpen"
      class="ai-chat-fab"
      :class="{ configured: isConfigured }"
      :style="fabStyle"
      @mousedown="startFabDrag"
      @click="toggleChat"
      title="AI 分析助手"
    >
      <span class="ai-chat-fab-icon">AI</span>
    </button>

    <div v-if="isOpen" ref="panelRef" class="ai-chat-panel" :style="panelStyle">
      <div class="ai-chat-header" @mousedown="startDrag">
        <div class="ai-chat-header-left">
          <span class="ai-chat-title">AI 赛事分析</span>
          <span class="ai-chat-game-badge">{{ gameLabel }}</span>
        </div>
        <div class="ai-chat-header-right">
          <button class="ai-chat-header-btn" title="清空对话" @click="clearChat">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <polyline points="1 4 1 10 7 10" /><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10" />
            </svg>
          </button>
          <button class="ai-chat-header-btn" title="收起" @click="toggleChat">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <polyline points="18 15 12 9 6 15" />
            </svg>
          </button>
        </div>
      </div>

      <div ref="chatBodyRef" class="ai-chat-body">
        <div v-if="!isConfigured" class="ai-chat-notice">
          <p>AI 助手尚未配置 API Key。</p>
          <p>请在 <code>backend/.env</code> 中设置 <code>DEEPSEEK_API_KEY</code>。</p>
        </div>

        <div
          v-for="(msg, idx) in messages"
          :key="idx"
          class="ai-chat-msg"
          :class="msg.role === 'user' ? 'msg-user' : 'msg-assistant'"
        >
          <div class="ai-chat-bubble">
            <div
              v-if="msg.content"
              class="ai-chat-bubble-text"
              v-html="renderContent(msg.content)"
            />
            <div v-else-if="msg.role === 'assistant' && isStreaming" class="ai-chat-typing">
              <span></span><span></span><span></span>
            </div>
            <div class="ai-chat-bubble-time">{{ msg.time }}</div>
          </div>
        </div>
      </div>

      <div class="ai-chat-footer">
        <input
          ref="inputRef"
          v-model="inputText"
          class="ai-chat-input"
          :disabled="!isConfigured"
          placeholder="输入你的问题，Enter 发送..."
          @keydown="handleKeydown"
        />
        <button
          v-if="isStreaming"
          class="ai-chat-stop-btn"
          title="停止生成"
          @click="stopStream"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
            <rect x="4" y="4" width="16" height="16" rx="2" />
          </svg>
        </button>
        <button
          v-else
          class="ai-chat-send-btn"
          :disabled="!isConfigured || !inputText.trim()"
          @click="sendMessage"
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
            <path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z" />
          </svg>
        </button>
      </div>
      <div class="ai-chat-resize" @mousedown="startResize" title="拖动调整大小">
        <svg width="12" height="12" viewBox="0 0 12 12" fill="currentColor" opacity="0.4">
          <path d="M11 0v1.5L1.5 11H0v-1.5L9.5 0H11zm0 4v1.5L5.5 11H4l7-7zm0 4v1.5L9.5 11H8l3-3z"/>
        </svg>
      </div>
    </div>
  </div>
</template>
