<template>
  <div class="dashboard">
    <header>
      <h1>🌾 AgriMeshAI</h1>
      <span class="user">{{ user }}</span>
      <button @click="logout">Logout</button>
    </header>

    <!-- Farm card -->
    <section class="farm-card" v-if="dashboard.farm">
      <h2>{{ dashboard.farm.name }}</h2>
      <p>{{ dashboard.farm.location }}</p>
      <p class="last-seen" :class="{ online: isOnline }">
        {{ isOnline ? '🟢 Online' : '🔴 Offline' }}
        — Last seen: {{ lastSeen }}
      </p>
    </section>

    <!-- Latest readings -->
    <section class="readings">
      <h3>Latest Readings</h3>
      <div class="sensor-grid">
        <div v-for="r in latestByNode" :key="r.key" class="sensor-card">
          <span class="label">{{ r.label }}</span>
          <span class="value">{{ r.value }}{{ r.unit }}</span>
        </div>
      </div>
    </section>

    <!-- Recent alerts -->
    <section class="alerts">
      <h3>Recent Alerts</h3>
      <div v-if="dashboard.recent_alerts?.length">
        <div v-for="a in dashboard.recent_alerts" :key="a.time" class="alert-item" :class="a.severity?.toLowerCase()">
          <span class="severity">{{ a.severity }}</span>
          <span v-if="a.node_id">Node {{ a.node_id }}:</span>
          <span>{{ a.message }}</span>
        </div>
      </div>
      <p v-else class="empty">No recent alerts</p>
    </section>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { getFarmDashboard } from '../api'

const user = ref(localStorage.getItem('username') || 'User')
const dashboard = ref({})
const farmId = 1

const isOnline = computed(() => {
  const ls = dashboard.value.farm?.last_seen
  if (!ls) return false
  return (Date.now() - new Date(ls).getTime()) < 120_000
})

const lastSeen = computed(() => {
  const ls = dashboard.value.farm?.last_seen
  return ls ? new Date(ls).toLocaleTimeString('vi-VN') : 'N/A'
})

const latestByNode = computed(() => {
  const readings = dashboard.value.latest_readings || []
  const sensorNames = { 0: 'Temp', 1: 'Humidity' }
  return readings.map(r => ({
    key: `${r.node_id}-${r.sensor_id}`,
    label: `Node ${r.node_id} ${sensorNames[r.sensor_id] || `S${r.sensor_id}`}`,
    value: r.value?.toFixed(1), unit: r.unit || ''
  }))
})

onMounted(async () => {
  try { const { data } = await getFarmDashboard(farmId); dashboard.value = data } catch (e) { console.error(e) }
})

function logout() {
  localStorage.clear()
  window.location.href = '/'
}
</script>

<style scoped>
.dashboard { max-width: 900px; margin: 0 auto; padding: 1rem; font-family: system-ui; }
header { display: flex; align-items: center; gap: 1rem; margin-bottom: 1.5rem; }
header h1 { margin: 0; font-size: 1.5rem; }
.user { margin-left: auto; color: #666; }
button { padding: 0.4rem 1rem; border: 1px solid #ccc; border-radius: 6px; cursor: pointer; background: #f5f5f5; }
.farm-card { background: #e8f5e9; padding: 1rem; border-radius: 8px; margin-bottom: 1.5rem; }
.farm-card h2 { margin: 0 0 0.25rem; }
.last-seen { margin-top: 0.5rem; font-size: 0.9rem; }
.last-seen.online { color: #2e7d32; }
.last-seen:not(.online) { color: #c62828; }
.sensor-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 0.75rem; }
.sensor-card { background: #fff; border: 1px solid #e0e0e0; border-radius: 8px; padding: 1rem; text-align: center; }
.sensor-card .label { display: block; font-size: 0.8rem; color: #888; margin-bottom: 0.25rem; }
.sensor-card .value { font-size: 1.5rem; font-weight: 700; color: #333; }
.alerts { margin-top: 1.5rem; }
.alert-item { padding: 0.5rem 0.75rem; border-radius: 6px; margin-bottom: 0.25rem; font-size: 0.9rem; }
.alert-item.critical { background: #ffebee; color: #c62828; }
.alert-item.warning { background: #fff8e1; color: #e65100; }
.alert-item.info { background: #e3f2fd; color: #1565c0; }
.severity { font-weight: 700; margin-right: 0.5rem; }
.empty { color: #999; }
</style>
