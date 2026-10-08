import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { api, onTokens, tokens, unwrap } from '@/api/client'
import type { TokenPair } from '@/api/types'

export type Permission =
  | 'map:view'
  | 'sensors:view'
  | 'sensors:edit'
  | 'layout:edit'
  | 'alerts:ack'
  | 'kpi:view'
  | 'people:view_pii'
  | 'whitelist:edit'
  | 'connectors:manage'

/** Permissions are in the access token (`perm` claim); decoding is only for showing/hiding UI. */
function permissionsOf(pair: TokenPair | null): string[] {
  if (!pair) return []
  try {
    const payload = pair.access_token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')
    const bytes = Uint8Array.from(atob(payload), (c) => c.charCodeAt(0))
    return JSON.parse(new TextDecoder().decode(bytes)).perm ?? []
  } catch {
    return []
  }
}

export const useAuth = defineStore('auth', () => {
  const pair = ref<TokenPair | null>(tokens.get())
  onTokens((p) => (pair.value = p))

  const user = computed(() => pair.value?.user ?? null)
  const permissions = computed(() => permissionsOf(pair.value))
  const loggedIn = computed(() => pair.value !== null)

  function can(p: Permission): boolean {
    return permissions.value.includes('*') || permissions.value.includes(p)
  }

  async function login(username: string, password: string) {
    tokens.set(unwrap(await api.POST('/auth/login', { body: { username, password } })))
  }

  function logout() {
    tokens.set(null)
  }

  return { pair, user, permissions, loggedIn, can, login, logout }
})

export const ROLE_NAMES: Record<string, string> = {
  dispatcher: 'Диспетчер',
  security: 'Охрана',
  admin: 'Администратор',
}
