import axios from 'axios'

const api = axios.create({ baseURL: '/api' })

// Attach JWT token
api.interceptors.request.use(config => {
  const token = localStorage.getItem('token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

// Auth
export const login = (username, password) => api.post('/auth/login', new URLSearchParams({ username, password }))
export const register = (data) => api.post('/auth/register', data)

// Farms
export const getFarms = () => api.get('/farms')
export const getFarm = (id) => api.get(`/farms/${id}`)
export const getFarmDashboard = (id) => api.get(`/farms/${id}/dashboard`)
export const getLatestReadings = (id) => api.get(`/farms/${id}/readings/latest`)
export const getReadings = (id, params) => api.get(`/farms/${id}/readings`, { params })
export const getAlerts = (id, params) => api.get(`/farms/${id}/alerts`, { params })
export const getNodes = (id) => api.get(`/farms/${id}/nodes`)

// Relay
export const controlRelay = (farmId, nodeId, relayId, state, durationS = 0) =>
  api.post(`/farms/${farmId}/relay`, { node_id: nodeId, relay_id: relayId, state, duration_s: durationS })

export default api
