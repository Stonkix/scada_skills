<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { errorText } from '@/api/client'
import { useAuth } from '@/stores/auth'

const auth = useAuth()
const router = useRouter()
const route = useRoute()
const username = ref('dispatcher')
const password = ref('')
const error = ref('')
const busy = ref(false)

const demo = [
  { user: 'dispatcher', label: 'Диспетчер' },
  { user: 'security', label: 'Охрана' },
  { user: 'admin', label: 'Админ' },
]

async function submit() {
  busy.value = true
  error.value = ''
  try {
    await auth.login(username.value, password.value)
    router.replace(typeof route.query.next === 'string' ? route.query.next : '/')
  } catch (e) {
    error.value = errorText(e)
  } finally {
    busy.value = false
  }
}

function useDemo(user: string) {
  username.value = user
  password.value = 'demo'
  submit()
}
</script>

<template>
  <div class="login">
    <form class="panel card" @submit.prevent="submit">
      <div class="row head">
        <svg viewBox="0 0 24 24" width="28" height="28"><path d="M12 3 L19 20 L12 16 L5 20 Z" fill="var(--accent)" /></svg>
        <div>
          <h1>SCADA · Логистика</h1>
          <div class="muted">Мониторинг инфраструктуры предприятия</div>
        </div>
      </div>
      <label class="field">Логин<input v-model="username" autocomplete="username" required /></label>
      <label class="field">Пароль<input v-model="password" type="password" autocomplete="current-password" required /></label>
      <div v-if="error" class="error">{{ error }}</div>
      <button class="primary" type="submit" :disabled="busy">{{ busy ? 'Вход…' : 'Войти' }}</button>
      <div class="demo">
        <span class="muted">Демо-доступ (пароль demo):</span>
        <div class="row wrap">
          <button v-for="d in demo" :key="d.user" type="button" class="small" @click="useDemo(d.user)">{{ d.label }}</button>
        </div>
      </div>
    </form>
  </div>
</template>

<style scoped>
.login {
  height: 100%;
  display: grid;
  place-items: center;
  padding: 16px;
  background: radial-gradient(1200px 600px at 70% -10%, #12305a55, transparent), var(--bg);
}
.card {
  width: min(380px, 100%);
  padding: 24px;
  display: grid;
  gap: 14px;
}
.head {
  gap: 12px;
  margin-bottom: 6px;
}
.error {
  color: #fecaca;
  background: #3b1219;
  border: 1px solid #7f1d1d;
  padding: 8px 10px;
  border-radius: 6px;
  font-size: 13px;
}
button.primary {
  justify-content: center;
  padding: 9px;
}
.demo {
  display: grid;
  gap: 6px;
  font-size: 12px;
  border-top: 1px solid var(--border);
  padding-top: 12px;
}
</style>
