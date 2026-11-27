<script setup lang="ts">
import {
  ArrowRight,
  Bell,
  Calendar,
  Cloudy,
  Document,
  Film,
  FolderOpened,
  HomeFilled,
  Link,
  Menu,
  Picture,
  Search,
  Setting,
  User,
  VideoPlay,
} from '@element-plus/icons-vue'
import type { RouteRecordRaw } from 'vue-router'

defineProps<{
  items: RouteRecordRaw[]
  activePath: string
  collapse?: boolean
  runningRouteNames?: string[]
  runningTooltip?: string
}>()

const emit = defineEmits(['select'])

function isRouteRunning(propsNames: string[] | undefined, item: RouteRecordRaw) {
  return Boolean(propsNames?.includes(String(item?.name || '')))
}

const iconMap = {
  ArrowRight,
  Bell,
  Calendar,
  Cloudy,
  Document,
  Film,
  FolderOpened,
  HomeFilled,
  Link,
  Menu,
  Picture,
  Search,
  Setting,
  User,
  VideoPlay,
}

function routeIndex(path: string) {
  return path ? `/${path}`.replace(/\/+/g, '/') : '/'
}

function menuRouteIndex(route: any, parent?: any) {
  const current = String(route?.path || '').trim()
  if (!parent) return routeIndex(current)
  if (!current) return routeIndex(String(parent?.path || '').trim())
  if (current.startsWith('/')) return routeIndex(current)
  const base = String(parent?.path || '').trim()
  return routeIndex(`${base}/${current}`)
}
</script>

<template>
  <el-menu
    router
    :default-active="activePath"
    :collapse="collapse"
    :collapse-transition="false"
    class="app-menu"
    @select="emit('select')"
  >
    <template v-for="item in items" :key="String(item.name)">
      <el-sub-menu v-if="item.children?.some((child) => !child.meta?.hidden)" :index="routeIndex(item.path)">
        <template #title>
          <el-icon>
            <component :is="iconMap[item.meta?.icon as keyof typeof iconMap] || User" />
          </el-icon>
          <span>{{ item.meta?.title }}</span>
        </template>
        <el-menu-item
          v-for="child in item.children?.filter((c) => !c.meta?.hidden) || []"
          :key="String(child.name)"
          :index="menuRouteIndex(child, item)"
        >
          <el-icon>
            <component :is="iconMap[child.meta?.icon as keyof typeof iconMap] || iconMap[item.meta?.icon as keyof typeof iconMap] || User" />
          </el-icon>
          <template #title>
            <span>{{ child.meta?.title }}</span>
          </template>
        </el-menu-item>
      </el-sub-menu>
      <el-menu-item v-else :index="routeIndex(item.path)">
        <span class="nav-icon-wrap">
          <el-icon>
            <component :is="iconMap[item.meta?.icon as keyof typeof iconMap] || User" />
          </el-icon>
          <span
            v-if="collapse && isRouteRunning(runningRouteNames, item)"
            class="nav-running-dot"
            aria-hidden="true"
          />
        </span>
        <template #title>
          <span class="nav-item-label">{{ item.meta?.title }}</span>
          <span
            v-if="collapse && isRouteRunning(runningRouteNames, item)"
            class="visually-hidden-collapsed-suffix"
          >
            · {{ runningTooltip || '定时任务执行中' }}
          </span>
          <el-tooltip
            v-else-if="!collapse && isRouteRunning(runningRouteNames, item)"
            :content="runningTooltip || '定时任务执行中'"
            placement="right"
          >
            <span class="nav-running-pill" aria-label="定时任务执行中">
              <span class="nav-running-pill__dot" aria-hidden="true" />
              <span>执行中</span>
            </span>
          </el-tooltip>
        </template>
      </el-menu-item>
    </template>
  </el-menu>
</template>
