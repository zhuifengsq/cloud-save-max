<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import {
  ArrowRight,
  Calendar,
  Expand,
  Film,
  Fold,
  HomeFilled,
  Menu,
  Moon,
  Sunny,
  SwitchButton,
  VideoPlay,
} from '@element-plus/icons-vue'
import { useRoute, useRouter } from 'vue-router'

import NavMenu from '@/layouts/NavMenu.vue'
import { useTheme } from '@/composables/useTheme'
import { resetDynamicRoutes } from '@/router'
import { useAuthStore } from '@/stores/auth'
import logoCat from '@/assets/logo-cat.webp'


const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const CACHED_VIEWS = ['DashboardView', 'DramaCalendarView', 'MediaDiscoverView', 'DramaTaskView']
const listScrollPositions = new Map<typeof route.name, number>([
  ['MediaDiscover', 0],
  ['DramaTasks', 0],
])

watch(
  () => [route.name, route.fullPath] as const,
  async ([name, path], [previousName]) => {
    const leavingList = listScrollPositions.has(previousName)
    if (leavingList) listScrollPositions.set(previousName, window.scrollY)

    const scrollY = name === 'TaskCopywriting' && leavingList ? 0 : listScrollPositions.get(name)
    if (scrollY === undefined) return
    await nextTick()
    if (route.fullPath === path) window.scrollTo(0, scrollY)
  },
)

const breadcrumbs = computed(() =>
  route.matched.filter((item) => item.meta?.title && item.meta?.breadcrumb !== false),
)

// 侧边导航分上下两段并带小标题：影视工作区（常用入口 + 链接整理）、系统管理（配置类入口）。
// 段内仍按路由表的定义顺序展示，权限过滤沿用 auth.menuRoutes 的结果。
const PRIMARY_NAV_NAMES = ['Dashboard', 'DramaCalendar', 'MediaDiscover', 'DramaTasks', 'LinkExtractor']
const primaryNav = computed(() =>
  auth.menuRoutes.filter((item) => PRIMARY_NAV_NAMES.includes(String(item.name))),
)
const secondaryNav = computed(() =>
  auth.menuRoutes.filter((item) => !PRIMARY_NAV_NAMES.includes(String(item.name))),
)

const SIDEBAR_STORAGE_KEY = 'ui:sidebar-collapsed'
const sidebarCollapsed = ref(false)
try {
  sidebarCollapsed.value = localStorage.getItem(SIDEBAR_STORAGE_KEY) === '1'
} catch {
  // Ignore unavailable storage.
}

function toggleSidebar() {
  sidebarCollapsed.value = !sidebarCollapsed.value
  try {
    localStorage.setItem(SIDEBAR_STORAGE_KEY, sidebarCollapsed.value ? '1' : '0')
  } catch {
    // Ignore unavailable storage.
  }
}

const QUICK_TAB_META: Record<string, { label: string; to: string; icon: any }> = {
  Dashboard: { label: '工作台', to: '/', icon: HomeFilled },
  DramaCalendar: { label: '日历', to: '/tasks/calendar', icon: Calendar },
  MediaDiscover: { label: '发现', to: '/discover', icon: Film },
  DramaTasks: { label: '任务', to: '/tasks/drama', icon: VideoPlay },
}

const mobileQuickTabs = computed(() =>
  primaryNav.value
    .map((item) => {
      const name = String(item.name || '')
      const meta = QUICK_TAB_META[name]
      return meta ? { name, ...meta } : null
    })
    .filter((item): item is { name: string; label: string; to: string; icon: any } => Boolean(item)),
)

const mobileNavVisible = ref(false)
const { isDark } = useTheme()
const userInitial = computed(() => String(auth.user?.username || 'A').slice(0, 1).toUpperCase())

function collectRouteNames(routes: any[]) {
  const names: string[] = []
  const walk = (item: any) => {
    if (typeof item?.name === 'string') names.push(item.name)
    const children = Array.isArray(item?.children) ? item.children : []
    for (const child of children) walk(child)
  }
  for (const r of routes || []) walk(r)
  return names
}

function handleMenuSelect() {
  mobileNavVisible.value = false
}

async function handleLogout() {
  const routeNames = collectRouteNames(auth.dynamicRoutes)

  await auth.logout()
  resetDynamicRoutes(routeNames)
  router.replace('/login')
}
</script>

<template>
  <div class="app-shell" :class="{ 'app-shell--collapsed': sidebarCollapsed }">
    <a href="#main-content" class="skip-link">跳到主要内容</a>
    <aside class="app-aside">
      <div class="glass-panel app-nav-card">
        <div class="app-logo">
          <div class="app-logo__mark">
            <img :src="logoCat" alt="追剧一体化平台" />
          </div>
          <div class="app-logo__text">
            <div class="app-logo__title">追剧一体化平台</div>
            <div class="app-logo__subtitle">Automation Console</div>
          </div>
          <el-tooltip :content="sidebarCollapsed ? '展开侧栏' : '收起侧栏'" :placement="sidebarCollapsed ? 'right' : 'bottom'">
            <el-button
              text
              class="app-collapse-btn"
              :aria-label="sidebarCollapsed ? '展开侧栏' : '收起侧栏'"
              @click="toggleSidebar"
            >
              <el-icon><Expand v-if="sidebarCollapsed" /><Fold v-else /></el-icon>
            </el-button>
          </el-tooltip>
        </div>

        <nav class="app-nav-sections" aria-label="主导航">
          <div class="app-nav-section">
            <div class="app-nav-section__title">影视工作区</div>
            <NavMenu :items="primaryNav" :active-path="route.path" :collapse="sidebarCollapsed" />
          </div>
          <div class="app-nav-section">
            <div class="app-nav-section__title">系统管理</div>
            <NavMenu :items="secondaryNav" :active-path="route.path" :collapse="sidebarCollapsed" />
          </div>
        </nav>

        <div class="app-aside-actions">
          <div class="app-aside-actions__row">
            <span class="app-aside-actions__label">主题</span>
            <el-switch
              v-model="isDark"
              inline-prompt
              :active-icon="Moon"
              :inactive-icon="Sunny"
              aria-label="切换深浅主题"
              class="app-theme-switch"
            />
          </div>
          <div class="app-user-footer">
            <div class="app-user-avatar" aria-hidden="true">{{ userInitial }}</div>
            <div class="app-user-meta">
              <div class="app-user-name" :title="auth.user?.username || '已登录'">
                {{ auth.user?.username || '已登录' }}
              </div>
              <div class="app-user-role">{{ auth.roles.join(' / ') || '成员' }}</div>
            </div>
            <el-tooltip content="退出登录" placement="top">
              <el-button
                text
                aria-label="退出登录"
                class="app-logout-btn"
                @click="handleLogout"
              >
                <el-icon><SwitchButton /></el-icon>
              </el-button>
            </el-tooltip>
          </div>
        </div>
      </div>
    </aside>

    <el-drawer
      :model-value="mobileNavVisible"
      direction="ltr"
      size="320px"
      class="app-mobile-drawer"
      @close="mobileNavVisible = false"
    >
      <template #header>
        <div class="app-logo app-logo--drawer">
          <div class="app-logo__mark">
            <img :src="logoCat" alt="追剧一体化平台" />
          </div>
          <div>
            <div class="app-logo__title">追剧一体化平台</div>
            <div class="app-logo__subtitle">全自动化追剧平台</div>
          </div>
        </div>
      </template>
      <nav class="app-nav-sections" aria-label="移动端导航">
        <div class="app-nav-section">
          <div class="app-nav-section__title">影视工作区</div>
          <NavMenu
            class="app-menu--drawer"
            :items="primaryNav"
            :active-path="route.path"
            @select="handleMenuSelect"
          />
        </div>
        <div class="app-nav-section">
          <div class="app-nav-section__title">系统管理</div>
          <NavMenu
            class="app-menu--drawer"
            :items="secondaryNav"
            :active-path="route.path"
            @select="handleMenuSelect"
          />
        </div>
      </nav>
    </el-drawer>

    <main id="main-content" tabindex="-1" class="app-content">
      <header class="glass-panel app-header" :class="{ 'app-header--compact': breadcrumbs.length <= 1 }">
        <div class="app-header__left">
          <el-button text aria-label="打开导航菜单" class="app-header__menu" @click="mobileNavVisible = true">
            <el-icon><Menu /></el-icon>
          </el-button>
          <div class="app-header__meta">
            <div class="app-header__eyebrow">控制台</div>
            <div class="app-header__brand">
              <img :src="logoCat" alt="" class="app-header__brand-logo" />
              <span>追剧一体化平台</span>
            </div>
            <el-breadcrumb :separator-icon="ArrowRight">
              <el-breadcrumb-item
                v-for="(item, index) in breadcrumbs"
                :key="item.path"
                :to="index < breadcrumbs.length - 1 ? (item.path || '/') : undefined"
              >
                {{ item.meta?.title }}
              </el-breadcrumb-item>
            </el-breadcrumb>
          </div>
        </div>
        <div class="app-header__right">
          <el-switch
            v-model="isDark"
            inline-prompt
            :active-icon="Moon"
            :inactive-icon="Sunny"
            aria-label="切换深浅主题"
            class="app-header__theme app-theme-switch"
          />
          <div class="app-user-chip">
            <div class="app-user-chip__name">{{ auth.user?.username }}</div>
            <div class="app-user-chip__meta">{{ auth.roles.join(' / ') || '已登录' }}</div>
          </div>
          <el-button type="primary" class="desktop-only" @click="handleLogout">退出登录</el-button>
          <el-dropdown class="mobile-only" @command="handleLogout">
            <el-button text aria-label="账号菜单" class="app-header__user">
              <span aria-hidden="true">{{ userInitial }}</span>
            </el-button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="logout">退出登录</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </header>

      <div class="app-main">
        <RouterView v-slot="{ Component }">
          <keep-alive :include="CACHED_VIEWS">
            <component :is="Component" />
          </keep-alive>
        </RouterView>
      </div>
    </main>

    <nav class="mobile-tabbar" aria-label="移动端快捷导航">
      <router-link
        v-for="tab in mobileQuickTabs"
        :key="tab.name"
        :to="tab.to"
        class="mobile-tabbar__item"
        :class="{ 'is-active': route.path === tab.to }"
      >
        <el-icon><component :is="tab.icon" /></el-icon>
        <span>{{ tab.label }}</span>
      </router-link>
      <button type="button" class="mobile-tabbar__item" @click="mobileNavVisible = true">
        <el-icon><Menu /></el-icon>
        <span>更多</span>
      </button>
    </nav>
  </div>
</template>
