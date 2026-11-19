<script setup lang="ts">
import { computed } from 'vue'
import { ArrowDown, ArrowLeft, ArrowRight } from '@element-plus/icons-vue'

const props = defineProps<{
  currentPage: number
  pageSize: number
  total: number
  totalPages?: number
  label: string
  mobile: boolean
  disabled?: boolean
}>()

const emit = defineEmits<{ 'current-change': [page: number] }>()

const pageCount = computed(() => Math.max(1, props.totalPages ?? Math.ceil(props.total / props.pageSize)))
const selectedPage = computed({
  get: () => Math.min(props.currentPage, pageCount.value),
  set: (page) => emit('current-change', page),
})
</script>

<template>
  <nav class="responsive-pagination" :aria-label="label">
    <div v-if="mobile" class="responsive-pagination__controls">
      <button type="button" :disabled="disabled || selectedPage <= 1" @click="selectedPage--">
        <ArrowLeft aria-hidden="true" />
        上一页
      </button>
      <div class="responsive-pagination__picker">
        <select v-model="selectedPage" aria-label="跳转页码" :disabled="disabled || pageCount <= 1">
          <option v-for="page in pageCount" :key="page" :value="page">
            {{ page === selectedPage ? `第 ${page} / ${pageCount} 页` : `第 ${page} 页` }}
          </option>
        </select>
        <ArrowDown class="responsive-pagination__picker-icon" aria-hidden="true" />
      </div>
      <button type="button" :disabled="disabled || selectedPage >= pageCount" @click="selectedPage++">
        下一页
        <ArrowRight aria-hidden="true" />
      </button>
    </div>
    <slot v-else name="desktop">
      <el-pagination
        class="responsive-pagination__desktop"
        background
        layout="prev, pager, next"
        :current-page="currentPage"
        :page-size="pageSize"
        :total="total"
        :disabled="disabled"
        @current-change="emit('current-change', $event)"
      />
    </slot>
  </nav>
</template>

<style scoped>
.responsive-pagination {
  display: flex;
}

.responsive-pagination__desktop {
  margin-inline-start: auto;
}

.responsive-pagination__controls {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1.55fr) minmax(0, 1fr);
  gap: 8px;
  width: min(100%, 380px);
  margin-inline: auto;
}

.responsive-pagination__controls button,
.responsive-pagination__controls select {
  min-width: 44px;
  min-height: 44px;
  border: 1px solid var(--el-border-color-light);
  border-radius: 8px;
  background: var(--el-fill-color-blank);
  color: var(--el-text-color-primary);
  font: inherit;
  font-size: 13px;
  cursor: pointer;
}

.responsive-pagination__controls button {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  padding: 4px;
  color: var(--el-color-primary);
  font-weight: 600;
  white-space: nowrap;
}

.responsive-pagination__controls button:disabled,
.responsive-pagination__controls select:disabled {
  color: var(--el-text-color-placeholder);
  cursor: default;
}

.responsive-pagination__controls :is(button, select):focus-visible {
  outline: 2px solid var(--el-color-primary);
  outline-offset: 2px;
}

.responsive-pagination__picker {
  position: relative;
  display: grid;
  min-width: 0;
}

.responsive-pagination__picker select {
  width: 100%;
  padding: 4px 20px 4px 6px;
  appearance: none;
  text-align: center;
  font-variant-numeric: tabular-nums;
}

.responsive-pagination__controls svg {
  flex: none;
  width: 14px;
  height: 14px;
}

.responsive-pagination__picker-icon {
  position: absolute;
  inset-inline-end: 6px;
  top: 50%;
  transform: translateY(-50%);
  pointer-events: none;
  color: var(--el-text-color-secondary);
}
</style>
