<script setup lang="ts">
import axios from 'axios'
import { ElMessage } from 'element-plus'
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import logoCat from '@/assets/logo-cat.png'
import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const loading = ref(false)
const errorMessage = ref('')
const form = reactive({
  username: '',
  password: '',
})

async function handleSubmit() {
  if (loading.value) return
  if (!form.username.trim() || !form.password) {
    errorMessage.value = '请输入用户名和密码'
    return
  }
  loading.value = true
  errorMessage.value = ''
  try {
    await auth.login(form.username, form.password)
    ElMessage.success('登录成功')
    router.replace(String(route.query.redirect || '/'))
  } catch (error) {
    if (axios.isAxiosError(error)) {
      errorMessage.value =
        error.response?.data?.message || (error.response ? '登录失败，请检查账号状态' : '网络异常，请确认后端服务已启动')
    } else {
      errorMessage.value = '登录异常，请稍后重试'
    }
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <main class="login-page">
    <el-card class="login-card" shadow="hover">
      <template #header>
        <div class="login-brand">
          <img :src="logoCat" alt="" width="48" height="48" class="login-brand__logo" />
          <h1 class="login-card__title">追剧一体化平台</h1>
          <p class="login-card__subtitle">自动转存 · 追剧日历 · 影视发现</p>
        </div>
      </template>

      <el-form label-position="top" @submit.prevent="handleSubmit">
        <el-alert
          v-if="errorMessage"
          role="alert"
          :title="errorMessage"
          type="error"
          show-icon
          :closable="false"
          class="login-alert"
        />
        <el-form-item label="用户名">
          <el-input
            id="login-username"
            v-model="form.username"
            name="username"
            autocomplete="username"
            spellcheck="false"
            autocapitalize="off"
            enterkeyhint="next"
            required
            placeholder="请输入用户名"
          />
        </el-form-item>
        <el-form-item label="密码">
          <el-input
            id="login-password"
            v-model="form.password"
            name="password"
            type="password"
            autocomplete="current-password"
            show-password
            enterkeyhint="done"
            required
            placeholder="请输入密码"
          />
        </el-form-item>
        <el-button class="login-submit" type="primary" native-type="submit" :loading="loading">
          登录
        </el-button>
      </el-form>
    </el-card>
  </main>
</template>

<style scoped>
.login-page {
  background:
    radial-gradient(circle at 20% 20%, rgba(53, 102, 232, 0.08), transparent 45%),
    radial-gradient(circle at 80% 80%, rgba(53, 102, 232, 0.06), transparent 45%),
    var(--bg);
}

:root[data-theme='dark'] .login-page {
  background:
    radial-gradient(circle at 20% 20%, rgba(138, 171, 255, 0.1), transparent 45%),
    radial-gradient(circle at 80% 80%, rgba(138, 171, 255, 0.07), transparent 45%),
    var(--bg);
}

.login-brand {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  text-align: center;
}

.login-brand__logo {
  display: block;
  width: 48px;
  height: 48px;
  border-radius: 12px;
  object-fit: cover;
  box-shadow: var(--shadow);
}

.login-card__subtitle {
  margin: 0;
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.login-alert {
  margin-bottom: 16px;
}
</style>
