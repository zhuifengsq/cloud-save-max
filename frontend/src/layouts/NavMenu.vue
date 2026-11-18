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
}>()

const emit = defineEmits(['select'])

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
  <el-menu router :default-active="activePath" class="app-menu" @select="emit('select')">
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
          <span>{{ child.meta?.title }}</span>
        </el-menu-item>
      </el-sub-menu>
      <el-menu-item v-else :index="routeIndex(item.path)">
        <el-icon>
          <component :is="iconMap[item.meta?.icon as keyof typeof iconMap] || User" />
        </el-icon>
        <span>{{ item.meta?.title }}</span>
      </el-menu-item>
    </template>
  </el-menu>
</template>
