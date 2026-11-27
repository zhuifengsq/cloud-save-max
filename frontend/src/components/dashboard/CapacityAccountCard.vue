<script setup lang="ts">
import type { TagProps } from 'element-plus'

import DriveTypeIcon from '@/components/tasks/DriveTypeIcon.vue'
import type { DriveAccountItem } from '@/types/extensions'
import { formatBytes, formatDateTime, formatPercent } from '@/utils/capacity'

const props = withDefaults(
  defineProps<{
    account: DriveAccountItem
    compact?: boolean
  }>(),
  {
    compact: false,
  },
)

const usagePercent = computed(() => Math.round((props.account.usage_ratio || 0) * 100))
const thresholdRatio = computed(() => props.account.capacity_warning_threshold / 100)

const capacityState = computed<'unknown' | 'safe' | 'warning'>(() => {
  if (props.account.usage_ratio === null || props.account.usage_ratio === undefined) return 'unknown'
  return props.account.usage_ratio >= thresholdRatio.value ? 'warning' : 'safe'
})

const runtimeTag = computed<TagProps['type']>(() => {
  if (props.account.runtime_status === 'active') return 'success'
  if (props.account.runtime_status === 'error') return 'danger'
  return 'info'
})

const runtimeStatusLabel = computed(() => {
  const status = String(props.account.runtime_status || '').trim()
  if (!status) return '未探测'
  const labels: Record<string, string> = {
    active: '运行正常',
    error: '探测异常',
    auth_required: '待二次认证',
    pending_auth: '待二次认证',
    disabled: '已禁用',
  }
  return labels[status] || status
})
</script>

<template>
  <article class="glass-panel account-card" :class="{ 'account-card--compact': compact, 'account-card--managed': $slots.controls }">
    <div class="account-card__header">
      <div class="account-card__heading">
        <div class="account-card__title-row">
          <DriveTypeIcon :type="account.drive_type" :size="22" />
          <h3>{{ account.name }}</h3>
        </div>
        <div class="account-card__meta">
          <span>{{ account.profile?.nickname || account.profile?.username || '未命名账号' }}</span>（<span>{{ account.profile?.drive_name || account.drive_type }}</span>）
        </div>
      </div>

      <slot name="controls" />

      <div class="account-card__status">
        <el-tag v-if="account.is_default" type="success" effect="plain" round size="small" disable-transitions>默认账号</el-tag>
        <el-tag :type="account.enabled ? 'success' : 'info'" size="small" effect="plain" round disable-transitions>
          {{ account.enabled ? '启用' : '禁用' }}
        </el-tag>
        <el-tag :type="runtimeTag" size="small" effect="plain" round disable-transitions>
          {{ runtimeStatusLabel }}
        </el-tag>
      </div>
    </div>

    <div class="account-card__details">
      <div v-if="$slots.controls" class="account-card__label">网盘容量</div>
      <div class="account-card__capacity">
        <template v-if="capacityState !== 'unknown'">
          <div class="account-card__capacity-main">
            <span>{{ formatBytes(account.used_space) }}</span>
            <span class="account-card__capacity-total">/ {{ formatBytes(account.total_space) }}</span>
          </div>
          <div class="account-card__capacity-side">
            <span class="account-card__percent">{{ formatPercent(account.usage_ratio) }}</span>
            <span :class="['status-pill', capacityState === 'warning' ? 'status-pill--danger' : 'status-pill--success']">
              阈值 {{ account.capacity_warning_threshold }}%
            </span>
          </div>
          <el-progress
            :percentage="usagePercent"
            :stroke-width="compact ? 8 : 10"
            :show-text="false"
            :status="capacityState === 'warning' ? 'exception' : 'success'"
          />
        </template>
        <div v-else class="account-card__empty">该账号暂未获得容量数据，可先执行探测或等待适配器补充支持。</div>
      </div>
    </div>

    <div class="account-card__footer">
      <span>最近刷新：{{ formatDateTime(account.profile_updated_at || account.last_checked_at) }}</span>
      <span v-if="account.last_error" class="account-card__error">{{ account.last_error }}</span>
    </div>

    <footer v-if="$slots.actions" class="account-card__actions">
      <slot name="actions" />
    </footer>
  </article>
</template>

<style scoped>
.account-card:not(.account-card--managed) .account-card__header {
  align-items: flex-start;
}

.account-card__actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  flex-wrap: wrap;
}

.account-card__actions :slotted(.el-button) {
  margin: 0;
}

.account-card__capacity-total {
  margin-left: 4px;
}

.account-card--managed {
  min-width: 0;
  min-height: 260px;
  gap: 18px;
  padding: 22px;
}

.account-card--managed .account-card__header {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: start;
  gap: 18px 12px;
}

.account-card--managed .account-card__heading,
.account-card--managed .account-card__footer {
  overflow-wrap: anywhere;
}

.account-card--managed .account-card__title-row h3 {
  font-size: 18px;
  line-height: 1.35;
}

.account-card--managed .account-card__meta {
  margin-top: 6px;
  line-height: 1.5;
}

.account-card--managed .account-card__status {
  grid-column: 1 / -1;
  justify-content: flex-start;
  gap: 8px;
}

.account-card--managed .account-card__details {
  display: grid;
  gap: 8px;
}

.account-card__label {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  font-weight: 600;
}

.account-card--managed .account-card__capacity {
  gap: 8px;
  padding: 10px 12px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: var(--panel-radius);
  background: var(--el-fill-color-blank);
}

.account-card--managed .account-card__empty {
  font-size: 13px;
  line-height: 1.5;
}

.account-card--managed .account-card__footer {
  margin-top: auto;
}

.account-card--managed .account-card__actions :slotted(.el-button) {
  padding-inline: 12px;
}

@media (max-width: 767px) {
  .account-card--managed {
    min-height: auto;
    padding: 18px;
  }
}
</style>
