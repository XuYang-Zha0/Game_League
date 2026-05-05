<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { fetchAiChatStream } from '../data/platformService'

const props = defineProps({
  gameId: { type: String, default: 'cs2' },
  gameName: { type: String, default: 'CS2' },
  page: { type: String, default: 'home' },
  contextData: { type: Object, default: () => ({}) },
})

const API_BASE = '/api/ai'

const isOpen = ref(false)
const isConfigured = ref(false)
const inputText = ref('')
const isStreaming = ref(false)
const messages = ref([])
const chatBodyRef = ref(null)
const inputRef = ref(null)
let abortController = null

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
    const res = await fetch(`${API_BASE}/status`)
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
  isOpen.value = !isOpen.value
  if (isOpen.value && messages.value.length === 0) {
    addMessage('assistant', welcomeText.value)
  }
  if (isOpen.value) {
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
      @click="toggleChat"
      title="AI 分析助手"
    >
      <span class="ai-chat-fab-icon">AI</span>
    </button>

    <div v-if="isOpen" class="ai-chat-panel">
      <div class="ai-chat-header">
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
    </div>
  </div>
</template>
