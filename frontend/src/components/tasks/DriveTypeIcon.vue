<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import { DRIVE_TYPE_META } from '@/utils/driveType'
import type { DriveType } from '@/utils/driveType'
import quarkLogo from '@/assets/drives/quark.svg'
import baiduLogo from '@/assets/drives/baidu-netdisk.png'
import cloud115Logo from '@/assets/drives/115.ico'
import ucLogo from '@/assets/drives/uc.png'
import pan123Logo from '@/assets/drives/123pan.ico'
import cloud189Logo from '@/assets/drives/cloud189.png'
import aliyunLogo from '@/assets/drives/aliyun.png'
import xunleiLogo from '@/assets/drives/xunlei.png'
import guangyaLogo from '@/assets/drives/guangya.png'

const props = withDefaults(
  defineProps<{
    type?: DriveType | string | null
    size?: number
  }>(),
  { type: null, size: 18 },
)

// 静态导入让构建工具校验并打包本地图标，避免页面请求错误路径。
const ICON_URLS: Record<string, string> = {
  quark: quarkLogo,
  baidu: baiduLogo,
  '115': cloud115Logo,
  uc: ucLogo,
  '123pan': pan123Logo,
  cloud189: cloud189Logo,
  aliyun: aliyunLogo,
  xunlei: xunleiLogo,
  guangya: guangyaLogo,
}

const failedType = ref('')
watch(() => props.type, () => { failedType.value = '' })

const meta = computed(() => {
  const key = String(props.type || '').trim() as DriveType
  if (!key) return null
  return DRIVE_TYPE_META[key] || null
})

const iconUrl = computed(() => {
  const key = String(props.type || '').trim()
  return key ? ICON_URLS[key] || '' : ''
})

const sizePx = computed(() => Number(props.size) || 18)

const badgeStyle = computed(() => {
  const size = sizePx.value
  const short = meta.value?.short || '?'
  const fontSize = short.length >= 3 ? size * 0.4 : short.length === 2 ? size * 0.48 : size * 0.62
  return {
    width: `${size}px`,
    height: `${size}px`,
    backgroundColor: meta.value?.color || '#909399',
    fontSize: `${fontSize}px`,
    borderRadius: `${Math.max(4, Math.round(size * 0.24))}px`,
  }
})
</script>

<template>
  <el-tooltip v-if="meta" :content="meta.label" placement="top" :show-after="200">
    <img
      v-if="iconUrl && failedType !== String(type || '')"
      class="drive-type-logo"
      :src="iconUrl"
      :width="sizePx"
      :height="sizePx"
      :style="{ width: `${sizePx}px`, height: `${sizePx}px`, borderRadius: `${Math.max(4, Math.round(sizePx * 0.24))}px` }"
      alt=""
      draggable="false"
      @error="failedType = String(type || '')"
    />
    <span v-else class="drive-type-badge" :style="badgeStyle">{{ meta.short }}</span>
  </el-tooltip>
  <el-tooltip v-else-if="type" content="未知网盘" placement="top" :show-after="200">
    <span class="drive-type-badge" :style="badgeStyle">?</span>
  </el-tooltip>
</template>

<style scoped>
.drive-type-logo,
.drive-type-badge {
  flex-shrink: 0;
  vertical-align: middle;
  user-select: none;
}

.drive-type-logo {
  display: inline-block;
  object-fit: contain;
}

.drive-type-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  font-weight: 700;
  line-height: 1;
  letter-spacing: -0.5px;
}
</style>
