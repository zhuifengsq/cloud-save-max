<script setup lang="ts">
defineProps<{
  items: Array<{taskId: number; key: string; label: string; text: string; percent: number | null}>
  title: string
  canWrite: boolean
  correcting: Record<number, boolean>
  overlay?: boolean
}>()
const emit = defineEmits<{correct: [taskId: number]}>()
</script>

<template>
  <div class="saved-progress" :class="{'saved-progress--overlay': overlay}" :tabindex="items.length > 2 ? 0 : undefined" :aria-label="`${title} 网盘进度`">
    <div v-for="item in items" :key="item.key" class="saved-progress__item">
      <div class="saved-progress__meta">
        <span class="saved-progress__drive">
          <span>{{ item.label }}</span>
          <button
            v-if="canWrite" type="button" class="saved-progress__correct"
            :disabled="Boolean(correcting[item.taskId])" :aria-busy="Boolean(correcting[item.taskId])"
            :aria-label="`校正${title} ${item.label}已存进度`"
            title="完整读取此任务的保存目录后重新计算已存进度，不转存或删除文件"
            @click.stop="emit('correct', item.taskId)"
          >{{ correcting[item.taskId] ? '校正中' : '校正' }}</button>
        </span>
        <span class="saved-progress__text">{{ item.text || (item.percent != null ? `${item.percent}%` : '暂无进度') }}</span>
      </div>
      <el-progress v-if="item.percent != null" :percentage="item.percent" :stroke-width="3" :show-text="false" :aria-label="`${item.label}：${item.text || `${item.percent}%`}`" />
    </div>
  </div>
</template>

<style scoped>
.saved-progress {display: grid; gap: 10px; min-width: 0; width: 100%;}
.saved-progress__item {display: grid; gap: 7px; min-width: 0;}
.saved-progress__meta {display: flex; align-items: baseline; justify-content: space-between; gap: 5px;}
.saved-progress__drive {display: flex; align-items: baseline; gap: 4px; min-width: 0; color: var(--el-text-color-secondary); font-size: 11px; line-height: 1.4;}
.saved-progress__correct {border: 0; padding: 0; background: transparent; color: var(--el-color-primary); font-size: 10px; white-space: nowrap; cursor: pointer;}
.saved-progress__correct:disabled {opacity: .7; cursor: wait;}
.saved-progress__correct:hover {text-decoration: underline;}
.saved-progress__text {font-size: 11px; line-height: 1.4; text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap;}
.saved-progress--overlay {max-height: 82px; overflow: auto; scrollbar-width: thin;}
.saved-progress--overlay .saved-progress__drive {color: #d0d8e4;}
.saved-progress--overlay .saved-progress__correct {color: #a9cdff;}
.saved-progress--overlay .saved-progress__text {color: #fff; font-weight: 500;}
.saved-progress--overlay :deep(.el-progress-bar__outer) {background: rgba(255, 255, 255, .2);}
.saved-progress :deep(.el-progress-bar__inner) {background: #58c99a;}
@media (max-width: 768px) {.saved-progress--overlay {max-height: 74px; gap: 8px;}}
</style>
