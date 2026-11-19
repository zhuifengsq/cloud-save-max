<script setup lang="ts">
import { computed } from 'vue'
import { ArrowLeft, ArrowRight } from '@element-plus/icons-vue'

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
const pageOptions = computed(() => Array.from({ length: pageCount.value }, (_, index) => ({
  value: index + 1,
  label: `第 ${index + 1} 页`,
})))
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
      <el-select-v2
        v-model="selectedPage"
        class="responsive-pagination__page-select"
        aria-label="跳转页码"
        popper-class="responsive-pagination-dropdown"
        placement="bottom-start"
        :fallback-placements="['top-start']"
        :fit-input-width="true"
        :options="pageOptions"
        :item-height="44"
        :height="220"
        :disabled="disabled || pageCount <= 1"
      >
        <template #label>第 {{ selectedPage }} / {{ pageCount }} 页</template>
      </el-select-v2>
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

.responsive-pagination__controls button {
  min-width: 44px;
  min-height: 44px;
  border: 1px solid var(--el-border-color-light);
  border-radius: 8px;
  background: var(--el-fill-color-blank);
  color: var(--el-text-color-primary);
  font: inherit;
  font-size: 13px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  padding: 4px;
  color: var(--el-color-primary);
  font-weight: 600;
  white-space: nowrap;
}

.responsive-pagination__controls button:disabled {
  color: var(--el-text-color-placeholder);
  cursor: default;
}

.responsive-pagination__controls button:focus-visible {
  outline: 2px solid var(--el-color-primary);
  outline-offset: 2px;
}

.responsive-pagination__page-select {
  min-width: 44px;
}

.responsive-pagination__page-select :deep(.el-select__wrapper) {
  min-height: 44px;
  padding-inline: 8px;
  border-radius: 8px;
  font-size: 13px;
}

.responsive-pagination__page-select :deep(.el-select__placeholder) {
  text-align: center;
  font-variant-numeric: tabular-nums;
}

.responsive-pagination__controls button svg {
  flex: none;
  width: 14px;
  height: 14px;
}

:global(.responsive-pagination-dropdown .el-select-dropdown__item) {
  display: flex;
  align-items: center;
}

:global(.responsive-pagination-dropdown .el-select-dropdown__item.is-selected) {
  background: var(--el-color-primary-light-9);
}
</style>
