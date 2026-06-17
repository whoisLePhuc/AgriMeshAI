<template>
  <div class="login">
    <h1>🌾 AgriMeshAI</h1>
    <p>Smart Farm Dashboard</p>
    <form @submit.prevent="handleLogin">
      <input v-model="username" placeholder="Username" required />
      <input v-model="password" type="password" placeholder="Password" required />
      <button type="submit">Login</button>
    </form>
    <p v-if="error" class="error">{{ error }}</p>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { login } from '../api'

const username = ref('')
const password = ref('')
const error = ref('')

async function handleLogin() {
  try {
    const { data } = await login(username.value, password.value)
    localStorage.setItem('token', data.access_token)
    localStorage.setItem('username', data.username)
    window.location.href = '/dashboard'
  } catch (e) {
    error.value = 'Invalid username or password'
  }
}
</script>

<style scoped>
.login { max-width: 360px; margin: 5rem auto; padding: 2rem; text-align: center; font-family: system-ui; }
.login h1 { margin-bottom: 0; }
.login p { color: #888; margin-bottom: 1.5rem; }
input { display: block; width: 100%; margin-bottom: 0.75rem; padding: 0.6rem; border: 1px solid #ccc; border-radius: 6px; }
button { width: 100%; padding: 0.6rem; background: #2e7d32; color: white; border: none; border-radius: 6px; cursor: pointer; font-size: 1rem; }
.error { color: #c62828; margin-top: 0.5rem; }
</style>
