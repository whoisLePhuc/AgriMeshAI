import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  { path: '/', name: 'login', component: () => import('../views/Login.vue') },
  { path: '/dashboard', name: 'dashboard', component: () => import('../views/Dashboard.vue') },
]

export default createRouter({ history: createWebHistory(), routes })
