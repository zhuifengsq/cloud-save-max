<script setup lang="ts">
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { clearPythonSearchPluginCache, savePythonSearchPluginConfig } from '@/api/resourceSearch'
import type { PythonSearchPlugin } from '@/types/resourceSearch'
import { formatDateTime } from '@/utils/capacity'

const props = defineProps<{ plugin: PythonSearchPlugin; canWrite: boolean }>()
const emit = defineEmits<{ changed: [] }>()
const visible = ref(false)
const saving = ref(false)
const clearing = ref(false)
const documentUrl = ref('')
const driveTypes = ref<string[]>([])

function open() {
  documentUrl.value = props.plugin.config?.document_url || ''
  driveTypes.value = [...(props.plugin.config?.drive_types || ['quark', 'baidu', 'xunlei'])]
  visible.value = true
}

async function save() {
  if (!documentUrl.value.trim()) return ElMessage.warning('请填写 WPS 文档链接')
  if (!driveTypes.value.length) return ElMessage.warning('请至少选择一种网盘')
  saving.value = true
  try {
    await savePythonSearchPluginConfig(props.plugin.key, { document_url: documentUrl.value.trim(), drive_types: driveTypes.value })
    visible.value = false
    ElMessage.success('WPS 搜索配置已保存')
    emit('changed')
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.message || error?.message || '保存失败')
  } finally { saving.value = false }
}

async function clear() {
  if (clearing.value) return
  clearing.value = true
  try {
    const result = await clearPythonSearchPluginCache(props.plugin.key)
    ElMessage.success(result.message)
    emit('changed')
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.message || error?.message || '清除缓存失败')
  } finally { clearing.value = false }
}
</script>

<template>
  <div class="wps-settings">
    <div class="wps-settings__buttons">
      <el-button :disabled="!canWrite" @click="open">配置</el-button>
      <el-button :disabled="!canWrite || saving" :loading="clearing" @click="clear">清除缓存</el-button>
    </div>
    <span class="wps-settings__status">
      <template v-if="plugin.cache?.cached">
        {{ plugin.cache.expired ? '缓存已过期' : '已缓存' }} · {{ plugin.cache.count }} 条链接 · {{ formatDateTime(plugin.cache.updated_at) }}
      </template>
      <template v-else>暂无缓存，首次搜索时读取文档</template>
    </span>
    <el-dialog v-model="visible" title="WPS 文档搜索配置" width="min(580px, 94vw)" append-to-body :close-on-click-modal="!saving">
      <el-form label-position="top" @submit.prevent="save">
        <el-form-item label="WPS 文档链接">
          <el-input v-model="documentUrl" placeholder="https://www.kdocs.cn/l/…" :disabled="saving" />
          <p class="wps-settings__help">填写无需登录、无需密码即可查看的公开文档链接。</p>
        </el-form-item>
        <el-form-item label="返回的网盘类型">
          <el-checkbox-group v-model="driveTypes" :disabled="saving">
            <el-checkbox value="quark">夸克</el-checkbox>
            <el-checkbox value="baidu">百度</el-checkbox>
            <el-checkbox value="xunlei">迅雷</el-checkbox>
          </el-checkbox-group>
          <p class="wps-settings__help">可多选；全选即返回全部类型。任务搜索仍会按目标网盘继续筛选。</p>
        </el-form-item>
        <el-alert title="文档缓存 15 分钟，过期后在下一次搜索时更新。清除缓存后，下次搜索将重新读取。" type="info" :closable="false" />
      </el-form>
      <template #footer>
        <el-button :disabled="saving" @click="visible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存配置</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.wps-settings { display: flex; flex-wrap: wrap; align-items: center; gap: 10px 16px; min-width: 0; }
.wps-settings__buttons { display: flex; gap: 10px; }
.wps-settings__buttons .el-button + .el-button { margin-left: 0; }
.wps-settings__status, .wps-settings__help { color: var(--el-text-color-secondary); font-size: 12px; line-height: 1.6; }
.wps-settings__help { width: 100%; margin: 6px 0 0; }
</style>
