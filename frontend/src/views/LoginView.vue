<script setup lang="ts">
import axios from 'axios'
import { Calendar, Film, Moon, Sunny, VideoPlay } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import logoCat from '@/assets/logo-cat.webp'
import { useTheme } from '@/composables/useTheme'
import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const { isDark } = useTheme()

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
    <div class="login-theme-toggle">
      <el-switch
        v-model="isDark"
        inline-prompt
        :active-icon="Moon"
        :inactive-icon="Sunny"
        aria-label="切换深浅主题"
        class="app-theme-switch"
      />
    </div>

    <div class="login-shell">
      <aside class="login-hero" aria-hidden="true">
        <div class="login-hero__brand">
          <img :src="logoCat" alt="" width="52" height="52" class="login-hero__logo" />
          <div>
            <div class="login-hero__title">追剧一体化平台</div>
            <div class="login-hero__sub">自动化追剧与网盘转存控制台</div>
          </div>
        </div>

        <ul class="login-hero__features">
          <li>
            <span class="login-hero__icon"><el-icon><VideoPlay /></el-icon></span>
            <div>
              <strong>多网盘自动追更与智能换源</strong>
              <span>支持夸克、百度、阿里云盘等，自动校验并修复失效链接</span>
            </div>
          </li>
          <li>
            <span class="login-hero__icon"><el-icon><Calendar /></el-icon></span>
            <div>
              <strong>TMDB 排期同步与追剧日历</strong>
              <span>周视图与月视图清晰掌握每部剧集的更新与已存进度</span>
            </div>
          </li>
          <li>
            <span class="login-hero__icon"><el-icon><Film /></el-icon></span>
            <div>
              <strong>热门影视发现与一键建任务</strong>
              <span>整合热门榜单、豆瓣/TMDB 评分与全网资源快速检索</span>
            </div>
          </li>
        </ul>

        <div class="login-hero__foot">Automated Media &amp; Cloud Drive Workspace</div>
      </aside>

      <section class="login-panel">
        <div class="login-mobile-brand">
          <img :src="logoCat" alt="" width="48" height="48" class="login-brand__logo" />
          <div class="login-mobile-brand__name">追剧一体化平台</div>
          <p class="login-card__subtitle">自动转存 · 追剧日历 · 影视发现</p>
        </div>

        <div class="login-panel__head">
          <h1 class="login-panel__title">欢迎登录</h1>
          <p class="login-panel__desc">请输入管理员或成员账号进入控制台</p>
        </div>

        <el-form label-position="top" class="login-form" @submit.prevent="handleSubmit">
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
      </section>
    </div>
  </main>
</template>

<style scoped>
.login-page {
  position: relative;
  min-height: 100vh;
  min-height: 100dvh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  background:
    radial-gradient(circle at 18% 18%, rgba(53, 102, 232, 0.1), transparent 46%),
    radial-gradient(circle at 84% 82%, rgba(53, 102, 232, 0.08), transparent 46%),
    var(--bg);
}

:root[data-theme='dark'] .login-page {
  background:
    radial-gradient(circle at 18% 18%, rgba(138, 171, 255, 0.12), transparent 46%),
    radial-gradient(circle at 84% 82%, rgba(99, 102, 241, 0.09), transparent 46%),
    var(--bg);
}

.login-theme-toggle {
  position: absolute;
  top: 20px;
  right: 24px;
}

.login-shell {
  width: min(880px, 100%);
  display: grid;
  grid-template-columns: 1.08fr 0.92fr;
  border-radius: 18px;
  overflow: hidden;
  border: 1px solid var(--panel-border);
  box-shadow: var(--el-box-shadow);
  background: var(--card);
}

.login-hero {
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  gap: 32px;
  padding: 40px 36px;
  color: #f1f5f9;
  background:
    radial-gradient(circle at 15% 12%, rgba(96, 165, 250, 0.24), transparent 52%),
    radial-gradient(circle at 90% 90%, rgba(129, 140, 248, 0.2), transparent 50%),
    linear-gradient(145deg, #16233d 0%, #1d3260 55%, #162447 100%);
}

.login-hero__brand {
  display: flex;
  align-items: center;
  gap: 14px;
}

.login-hero__logo {
  display: block;
  width: 52px;
  height: 52px;
  border-radius: 14px;
  object-fit: cover;
  box-shadow: 0 8px 20px rgba(0, 0, 0, 0.28);
}

.login-hero__title {
  font-size: 20px;
  font-weight: 700;
  letter-spacing: -0.02em;
  color: #ffffff;
}

.login-hero__sub {
  margin-top: 4px;
  font-size: 13px;
  color: rgba(226, 232, 240, 0.8);
}

.login-hero__features {
  margin: 0;
  padding: 0;
  list-style: none;
  display: grid;
  gap: 18px;
}

.login-hero__features li {
  display: flex;
  align-items: flex-start;
  gap: 12px;
}

.login-hero__icon {
  flex: 0 0 auto;
  width: 32px;
  height: 32px;
  border-radius: 10px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: rgba(255, 255, 255, 0.12);
  border: 1px solid rgba(255, 255, 255, 0.18);
  color: #93c5fd;
  font-size: 16px;
}

.login-hero__features strong {
  display: block;
  font-size: 14px;
  font-weight: 600;
  color: #ffffff;
}

.login-hero__features span:last-child {
  display: block;
  margin-top: 3px;
  font-size: 12.5px;
  line-height: 1.5;
  color: rgba(226, 232, 240, 0.78);
}

.login-hero__foot {
  font-size: 11px;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: rgba(226, 232, 240, 0.52);
}

.login-panel {
  display: flex;
  flex-direction: column;
  justify-content: center;
  padding: 40px 36px;
  background: var(--card);
}

.login-mobile-brand {
  display: none;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  margin-bottom: 24px;
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

.login-mobile-brand__name {
  font-size: 20px;
  font-weight: 700;
  color: var(--ink);
}

.login-card__subtitle {
  margin: 0;
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.login-panel__head {
  margin-bottom: 24px;
}

.login-panel__title {
  margin: 0;
  font-size: 22px;
  font-weight: 700;
  letter-spacing: -0.02em;
  color: var(--ink);
}

.login-panel__desc {
  margin: 6px 0 0;
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.login-form :deep(.el-input) {
  --el-component-size: 42px;
}

.login-alert {
  margin-bottom: 16px;
}

.login-submit {
  width: 100%;
  height: 42px;
  margin-top: 8px;
}

@media (max-width: 859px) {
  .login-page {
    padding: 16px;
  }

  .login-theme-toggle {
    top: 14px;
    right: 16px;
  }

  .login-shell {
    width: min(420px, 100%);
    grid-template-columns: 1fr;
  }

  .login-hero {
    display: none;
  }

  .login-panel {
    padding: 28px 22px;
  }

  .login-mobile-brand {
    display: flex;
  }

  .login-panel__head {
    display: none;
  }
}
</style>
