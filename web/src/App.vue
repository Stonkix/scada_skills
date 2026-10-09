<script setup lang="ts">
import { computed, watch } from 'vue'
import { RouterLink, RouterView, useRoute, useRouter } from 'vue-router'
import ToastStack from '@/components/ToastStack.vue'
import { ROLE_NAMES, useAuth } from '@/stores/auth'
import { useLive } from '@/stores/live'
import { useObjects } from '@/stores/objects'

const auth = useAuth()
const live = useLive()
const objects = useObjects()
const route = useRoute()
const router = useRouter()

const nav = computed(() =>
  [
    { to: '/', label: 'Карта', show: auth.can('map:view') },
    { to: '/real', label: '3D-карта', show: auth.can('map:view') },
    { to: '/alerts', label: 'Тревоги', show: auth.can('map:view') },
    { to: '/kpi', label: 'KPI', show: auth.can('kpi:view') },
    { to: '/analytics', label: 'Replay', show: auth.can('kpi:view') },
    { to: '/registry', label: 'Реестр', show: auth.can('sensors:view') },
    { to: '/connectors', label: 'Коннекторы', show: auth.can('sensors:view') },
    { to: '/editor', label: 'Редактор', show: auth.can('layout:edit') },
  ].filter((n) => n.show),
)
const openCount = computed(() => live.openAlerts.filter((a) => a.status === 'open').length)

// The live connection lives as long as the session: every page shows the alert counter and toasts.
watch(
  () => auth.loggedIn,
  async (on) => {
    if (on) {
      await objects.load()
      await live.start()
    } else {
      live.stop()
      objects.data = null
      if (!route.meta.public) router.push({ name: 'login' })
    }
  },
  { immediate: true },
)

function logout() {
  auth.logout()
}
</script>

<template>
  <div class="shell" :class="{ bare: route.meta.public }">
    <header v-if="!route.meta.public" class="topbar">
      <RouterLink to="/" class="brand">
        <svg viewBox="0 0 24 24" width="20" height="20"><path d="M12 3 L19 20 L12 16 L5 20 Z" fill="var(--accent)" /></svg>
        <span>SCADA&nbsp;<b>Логистика</b></span>
      </RouterLink>
      <nav>
        <RouterLink v-for="n in nav" :key="n.to" :to="n.to" class="nav-link">
          {{ n.label }}
          <span v-if="n.to === '/alerts' && openCount" class="count" :class="{ crit: live.criticalCount }">{{ openCount }}</span>
        </RouterLink>
      </nav>
      <div class="spacer" />
      <span class="conn" :class="live.status" :title="`WebSocket: ${live.status}`">
        <i />{{ live.status === 'open' ? 'online' : live.status === 'connecting' ? 'подключение…' : 'offline' }}
      </span>
      <button class="ghost small" :title="live.soundOn ? 'Звук тревог включён' : 'Звук тревог выключен'" @click="live.soundOn = !live.soundOn">
        {{ live.soundOn ? '🔔' : '🔕' }}
      </button>
      <div v-if="auth.user" class="user">
        <span>{{ auth.user.full_name }}</span>
        <small class="muted">{{ ROLE_NAMES[auth.user.role] ?? auth.user.role }}</small>
      </div>
      <button class="ghost small" @click="logout">Выйти</button>
    </header>
    <main>
      <RouterView />
    </main>
    <ToastStack />
  </div>
</template>

<style scoped>
.shell {
  height: 100%;
  display: grid;
  grid-template-rows: auto 1fr;
}
.shell.bare {
  grid-template-rows: 1fr;
}
main {
  min-height: 0;
  position: relative;
}
.topbar {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 0 14px;
  height: 48px;
  background: var(--panel);
  border-bottom: 1px solid var(--border);
}
.brand {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--text);
  text-decoration: none;
  margin-right: 8px;
  white-space: nowrap;
}
nav {
  display: flex;
  gap: 2px;
  overflow-x: auto;
}
.nav-link {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  border-radius: 6px;
  color: var(--text-muted);
  text-decoration: none;
  white-space: nowrap;
}
.nav-link:hover {
  color: var(--text);
}
.nav-link.router-link-exact-active {
  color: var(--text);
  background: var(--panel-2);
}
.count {
  font: 600 11px/1 var(--mono);
  padding: 3px 6px;
  border-radius: 9px;
  background: var(--st-warning);
  color: #111;
}
.count.crit {
  background: var(--st-critical);
  color: #fff;
}
.conn {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--text-muted);
}
.conn i {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--st-offline);
}
.conn.open i {
  background: var(--st-ok);
  box-shadow: 0 0 6px var(--st-ok);
}
.conn.connecting i {
  background: var(--st-warning);
}
.user {
  display: grid;
  line-height: 1.15;
  text-align: right;
  font-size: 12px;
}
@media (max-width: 900px) {
  .user,
  .conn {
    display: none;
  }
  .brand span {
    display: none;
  }
}
</style>
