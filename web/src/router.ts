import { createRouter, createWebHistory } from 'vue-router'
import { useAuth, type Permission } from '@/stores/auth'

declare module 'vue-router' {
  interface RouteMeta {
    title?: string
    permission?: Permission
    public?: boolean
  }
}

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', name: 'login', component: () => import('@/views/LoginView.vue'), meta: { public: true, title: 'Вход' } },
    { path: '/', name: 'map', component: () => import('@/views/MapView.vue'), meta: { permission: 'map:view', title: 'Карта' } },
    { path: '/real', name: 'real', component: () => import('@/views/RealMapView.vue'), meta: { permission: 'map:view', title: '3D-карта' } },
    { path: '/alerts', name: 'alerts', component: () => import('@/views/AlertsView.vue'), meta: { permission: 'map:view', title: 'Тревоги' } },
    { path: '/kpi', name: 'kpi', component: () => import('@/views/KpiView.vue'), meta: { permission: 'kpi:view', title: 'KPI' } },
    { path: '/analytics', name: 'analytics', component: () => import('@/views/AnalyticsView.vue'), meta: { permission: 'kpi:view', title: 'Replay и тепловая карта' } },
    { path: '/registry', name: 'registry', component: () => import('@/views/RegistryView.vue'), meta: { permission: 'sensors:view', title: 'Реестр датчиков' } },
    { path: '/editor', name: 'editor', component: () => import('@/views/EditorView.vue'), meta: { permission: 'layout:edit', title: 'Редактор плана' } },
    { path: '/connectors', name: 'connectors', component: () => import('@/views/ConnectorsView.vue'), meta: { permission: 'sensors:view', title: 'Коннекторы' } },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})

router.beforeEach((to) => {
  const auth = useAuth()
  if (to.meta.public) return auth.loggedIn && to.name === 'login' ? { name: 'map' } : true
  if (!auth.loggedIn) return { name: 'login', query: { next: to.fullPath } }
  if (to.meta.permission && !auth.can(to.meta.permission)) return { name: 'map' }
  return true
})

router.afterEach((to) => {
  document.title = to.meta.title ? `${to.meta.title} · SCADA` : 'SCADA'
})
