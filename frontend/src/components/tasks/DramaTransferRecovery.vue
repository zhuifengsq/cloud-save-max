<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { computed, ref, useId, watch } from 'vue'
import { fetchDramaTransferRecovery, resolveDramaTransferRecovery } from '@/api/tasks'
import { TASK_WRITE } from '@/constants/permissions'
import { useAuthStore } from '@/stores/auth'
import type { DramaTransferRecovery } from '@/types/tasks'

const props = defineProps<{ taskId?: number; visible: boolean }>()
const activeId = computed(() => props.visible ? props.taskId : undefined)
const auth = useAuthStore()
const dialogId = useId()
const snapshot = ref<DramaTransferRecovery | null>(null)
const loadFailed = ref(false)
const busy = ref(false)
const refresh = ref(0)
const discardDialog = ref<HTMLDialogElement | null>(null)
const selected = ref<{ taskId: number; version: string; directory?: string } | null>(null)

watch([activeId, refresh], ([taskId], _, onCleanup) => {
  const controller = new AbortController()
  onCleanup(() => controller.abort())
  snapshot.value = null
  loadFailed.value = false
  if (!taskId) return
  void fetchDramaTransferRecovery(taskId, controller.signal)
    .then((value) => {
      if (!controller.signal.aborted) snapshot.value = value
    })
    .catch(() => {
      if (!controller.signal.aborted) loadFailed.value = true
    })
}, { immediate: true })

async function resolve(action: 'retry' | 'abandon' | 'abandon_directory', taskId: number, version: string, directory?: string) {
  if (busy.value || taskId !== activeId.value || version !== snapshot.value?.version) return
  busy.value = true
  try {
    await resolveDramaTransferRecovery(taskId, version, action, directory)
    if (action === 'abandon_directory') ElMessage.success('已放弃这个目录的续跑记录')
    else ElMessage.success(action === 'retry' ? '已恢复确认，下次执行时继续校验' : '已放弃旧断点，下次执行将重新规划')
  } catch {
    // The API error toast explains a changed checkpoint or an overlapping run.
  } finally {
    busy.value = false
    if (taskId === activeId.value) refresh.value++
  }
}

function retry() {
  if (activeId.value && snapshot.value?.can_retry) {
    void resolve('retry', activeId.value, snapshot.value.version)
  }
}

let supportsDisplayTransition: boolean | undefined

function canTransitionDisplay() {
  if (supportsDisplayTransition !== undefined) return supportsDisplayTransition
  if (typeof window === 'undefined' || !window.CSS?.supports?.('transition-behavior', 'allow-discrete') || !document.body) {
    return false
  }
  const probe = document.createElement('div')
  probe.style.cssText = 'transition: display 1s allow-discrete !important; display: block;'
  document.body.appendChild(probe)
  getComputedStyle(probe).display
  probe.style.display = 'none'
  supportsDisplayTransition = getComputedStyle(probe).display === 'block'
  probe.remove()
  return supportsDisplayTransition
}

function supportsTopLayerExit() {
  return typeof window !== 'undefined' && Boolean(window.CSS?.supports?.('overlay', 'auto')) && canTransitionDisplay()
}

async function closeDiscardDialog(choice?: string) {
  const dialog = discardDialog.value
  if (!dialog || !dialog.open) return
  if (!supportsTopLayerExit()) {
    dialog.setAttribute('data-closing', '')
    const animations = dialog.getAnimations({ subtree: true })
    if (animations.length > 0) {
      await Promise.race([
        Promise.allSettled(animations.map((a) => a.finished)),
        new Promise((r) => setTimeout(r, 400)),
      ])
    }
    if (!dialog.hasAttribute('data-closing')) return
    dialog.removeAttribute('data-closing')
  }
  dialog.close(choice)
}

function confirmDiscard(directory?: string) {
  if (!activeId.value || !snapshot.value || !discardDialog.value) return
  discardDialog.value.removeAttribute('data-closing')
  selected.value = { taskId: activeId.value, version: snapshot.value.version, directory }
  discardDialog.value.returnValue = ''
  discardDialog.value.showModal()
}

function onDiscardCancel(event: Event) {
  event.preventDefault()
  void closeDiscardDialog('cancel')
}

function onDiscardSubmit(event: SubmitEvent) {
  event.preventDefault()
  const submitter = event.submitter as HTMLButtonElement | null
  void closeDiscardDialog(submitter?.value || 'cancel')
}

function onDiscardClosed(event: Event) {
  const dialog = event.currentTarget as HTMLDialogElement
  const choice = dialog.returnValue
  dialog.returnValue = ''
  const checkpoint = selected.value
  selected.value = null
  if (choice === 'abandon' && checkpoint) {
    void resolve(checkpoint.directory ? 'abandon_directory' : 'abandon',
      checkpoint.taskId, checkpoint.version, checkpoint.directory)
  }
}
</script>

<template>
  <section v-if="snapshot" class="transfer-recovery" :aria-labelledby="dialogId + '-status'">
    <strong :id="dialogId + '-status'">{{ snapshot.requires_confirmation ? '转存需人工确认' : '转存断点' }}</strong>
    <p>{{ snapshot.reason }}</p>
    <details v-if="snapshot.confirmed_count || snapshot.pending_names.length || snapshot.output_fids.length || snapshot.fallback_dirs.length">
      <summary>查看文件与目录</summary>
      <template v-if="snapshot.confirmed_count">
        <p>已确认转存 {{ snapshot.confirmed_count }} 项，以下显示前 {{ snapshot.confirmed_files.length }} 项</p>
        <ul>
          <li v-for="file in snapshot.confirmed_files" :key="file.fid">
            {{ file.name }}<template v-if="file.target_name !== file.name"> → {{ file.target_name }}</template>
          </li>
        </ul>
      </template>
      <p v-if="snapshot.pending_names.length">待确认 {{ snapshot.pending_names.length }} 项</p>
      <ul v-if="snapshot.pending_names.length">
        <li v-for="(name, index) in snapshot.pending_names" :key="index">{{ name }}</li>
      </ul>
      <p v-if="snapshot.output_fids.length">已取得 {{ snapshot.output_fids.length }} 个网盘文件 ID，等待核对目录</p>
      <template v-if="snapshot.fallback_dirs.length">
        <p>目录续跑记录</p>
        <ul>
          <li v-for="directory in snapshot.fallback_dirs" :key="directory">
            {{ directory }}
            <span v-if="snapshot.paused_fallback_dirs?.includes(directory)">（连续缺失，待处理）</span>
            <span v-else-if="snapshot.excluded_fallback_dirs?.includes(directory)">（按当前规则排除）</span>
            <el-button v-if="auth.permissions.includes(TASK_WRITE) && snapshot.discardable_fallback_dirs?.includes(directory)"
              link type="danger" :disabled="busy" @click="confirmDiscard(directory)">放弃目录续跑</el-button>
          </li>
        </ul>
      </template>
    </details>
    <div v-if="auth.permissions.includes(TASK_WRITE)" class="transfer-recovery__actions">
      <el-button v-if="snapshot.can_retry" size="small" :disabled="busy" @click="retry">恢复确认</el-button>
      <el-button size="small" type="danger" plain :disabled="busy" @click="confirmDiscard()">放弃旧断点</el-button>
    </div>
    <!-- Let the browser close this modal without also closing the parent editor. -->
    <dialog ref="discardDialog" class="transfer-recovery__dialog" :aria-labelledby="dialogId"
      @keydown.esc.stop @cancel="onDiscardCancel" @close="onDiscardClosed">
      <h3 :id="dialogId">{{ selected?.directory ? '放弃目录续跑' : '放弃旧转存断点' }}</h3>
      <p v-if="selected?.directory">放弃「{{ selected.directory }}」的续跑记录。目标同名目录以后可能被整包跳过，缺失的集数可能无法自动补齐。若希望目录重新出现后自动补集，请保留续跑记录。</p>
      <p v-else>请先核对网盘目标目录。已经转存的文件会保留，下次重新规划可能重复转存这些文件。</p>
      <form method="dialog" class="transfer-recovery__actions" @submit="onDiscardSubmit">
        <button class="el-button el-button--danger" type="submit" value="abandon">确认放弃</button>
        <button class="el-button" type="submit" value="cancel" autofocus>取消</button>
      </form>
    </dialog>
  </section>
  <p v-else-if="loadFailed" role="status">
    转存断点读取失败 <el-button link type="primary" @click="refresh++">重试</el-button>
  </p>
</template>

<style scoped>
.transfer-recovery {
  padding: 12px 16px;
  margin-bottom: 16px;
  border: 1px solid var(--el-color-warning-light-5);
  border-radius: var(--el-border-radius-base);
  overflow-wrap: anywhere;
}

.transfer-recovery__actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 12px;
}

.transfer-recovery__actions .el-button + .el-button {
  margin-left: 0;
}

.transfer-recovery__dialog:modal {
  box-sizing: border-box;
  max-width: min(420px, calc(100% - 24px));
  padding: 20px;
  border: 1px solid var(--el-border-color);
  border-radius: var(--el-border-radius-base);
  background: var(--el-bg-color);
  color: var(--el-text-color-primary);
  overscroll-behavior: contain;
  opacity: 0;
  transition-property: opacity, transform, display, overlay;
  transition-duration: 0.22s;
  transition-timing-function: ease-out;
  transition-behavior: allow-discrete;
}

@media (prefers-reduced-motion: no-preference) {
  .transfer-recovery__dialog:modal {
    transform: scale(0.96);
  }
}

.transfer-recovery__dialog[open]:where(:not([data-closing])) {
  opacity: 1;
}

@starting-style {
  .transfer-recovery__dialog[open]:where(:not([data-closing])) {
    opacity: 0;
  }
}

@media (prefers-reduced-motion: no-preference) {
  .transfer-recovery__dialog[open]:where(:not([data-closing])) {
    transform: scale(1);
  }

  @starting-style {
    .transfer-recovery__dialog[open]:where(:not([data-closing])) {
      transform: scale(0.96);
    }
  }
}

.transfer-recovery__dialog::backdrop {
  background: rgb(0 0 0 / 0);
  overflow: hidden;
  overscroll-behavior: contain;
  transition-property: background-color, display, overlay;
  transition-duration: 0.22s;
  transition-timing-function: ease-out;
  transition-behavior: allow-discrete;
}

.transfer-recovery__dialog[open]:where(:not([data-closing]))::backdrop {
  background: rgb(0 0 0 / 45%);
}

@starting-style {
  .transfer-recovery__dialog[open]:where(:not([data-closing]))::backdrop {
    background: rgb(0 0 0 / 0);
  }
}

@media (prefers-reduced-motion: reduce) {
  .transfer-recovery__dialog:modal,
  .transfer-recovery__dialog::backdrop {
    transition-duration: 0.08s;
  }
}
</style>
