<script setup lang="ts">
import { Refresh } from '@element-plus/icons-vue'

defineProps<{ title: string; loading: boolean; error?: string; compact?: boolean }>()
const emit = defineEmits<{ refresh: [] }>()
</script>

<template>
  <button
    type="button" class="drama-refresh" :class="{'is-compact': compact, 'has-error': error}"
    :disabled="loading" :aria-busy="loading" :aria-label="`更新${title}资料`"
    :title="error ? `${error}，点击重试` : loading ? '正在更新本剧资料' : '更新本剧资料'"
    @click.stop="emit('refresh')"
  >
    <Refresh class="drama-refresh__icon" :class="{'is-spinning': loading}" />
  </button>
</template>

<style scoped>
.drama-refresh {
  position: absolute;
  top: 8px;
  left: 8px;
  z-index: 2;
  display: grid;
  place-items: center;
  width: 32px;
  height: 32px;
  padding: 0;
  border: 1px solid rgba(255, 255, 255, .4);
  border-radius: 50%;
  background: rgba(15, 23, 42, .8);
  color: #fff;
  cursor: pointer;
}
.drama-refresh:hover, .drama-refresh:focus-visible { background: var(--el-color-primary); }
.drama-refresh:disabled { cursor: wait; }
.drama-refresh.has-error { color: #fca5a5; border-color: #fca5a5; }
.drama-refresh.is-compact { top: 0; left: 0; width: 20px; height: 20px; }
.drama-refresh__icon { width: 16px; height: 16px; }
.is-compact .drama-refresh__icon { width: 12px; height: 12px; }
.is-spinning { animation: drama-refresh-spin 1s linear infinite; }
@keyframes drama-refresh-spin { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) { .is-spinning { animation: none; } }
</style>
