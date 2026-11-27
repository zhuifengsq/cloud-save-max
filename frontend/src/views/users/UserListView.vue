<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { computed, onMounted, reactive, ref } from 'vue'

import RoleAssignDialog from '@/components/users/RoleAssignDialog.vue'
import ResponsivePagination from '@/components/ResponsivePagination.vue'
import { useIsMobile } from '@/composables/useIsMobile'
import { USER_WRITE } from '@/constants/permissions'
import {
  batchSetUserRoles,
  batchSetUserStatus,
  createUser,
  fetchRoles,
  fetchUsers,
  setUserRoles,
  setUserStatus,
} from '@/api/users'
import { useAuthStore } from '@/stores/auth'
import { isImeComposing } from '@/utils/ime'
import type { RoleItem, UserItem } from '@/types/user'

const auth = useAuthStore()
const canWrite = computed(() => auth.permissions.includes(USER_WRITE))
const isMobile = useIsMobile()

const loading = ref(false)
const users = ref<UserItem[]>([])
const total = ref(0)
const selectedRows = ref<UserItem[]>([])
const roles = ref<RoleItem[]>([])
const roleDialogVisible = ref(false)
const roleDialogTitle = ref('分配角色')
const currentRoleUserId = ref<number | null>(null)
const currentRoleIds = ref<number[]>([])

const query = reactive({
  page: 1,
  page_size: 10,
  q: '',
})

const createDialogVisible = ref(false)
const createForm = reactive({
  username: '',
  email: '',
  password: '',
})

async function loadRoles() {
  const data = await fetchRoles()
  roles.value = data.map((item) => ({
    id: item.id,
    name: item.name,
    description: item.description,
  }))
}

async function loadUsers() {
  loading.value = true
  try {
    const data = await fetchUsers({ ...query, q: query.q || undefined })
    users.value = data.items
    total.value = data.total
    selectedRows.value = []
  } finally {
    loading.value = false
  }
}

function isUserSelected(userId: number) {
  return selectedRows.value.some((item) => item.id === userId)
}

function toggleUserSelected(row: UserItem, checked: boolean) {
  if (checked) {
    if (!isUserSelected(row.id)) {
      selectedRows.value = [...selectedRows.value, row]
    }
  } else {
    selectedRows.value = selectedRows.value.filter((item) => item.id !== row.id)
  }
}

function handleSelectionChange(rows: UserItem[]) {
  selectedRows.value = rows
}

async function toggleStatus(row: UserItem, isActive: boolean) {
  await setUserStatus(row.id, isActive)
  ElMessage.success('状态已更新')
  await loadUsers()
}

function openRoleDialog(row?: UserItem) {
  currentRoleUserId.value = row?.id || null
  currentRoleIds.value = row?.roles.map((item) => item.id) || []
  roleDialogTitle.value = row ? `为 ${row.username} 分配角色` : '批量分配角色'
  roleDialogVisible.value = true
}

async function submitRoleDialog(roleIds: number[]) {
  if (currentRoleUserId.value) {
    await setUserRoles(currentRoleUserId.value, roleIds)
  } else {
    await batchSetUserRoles(
      selectedRows.value.map((item) => item.id),
      roleIds,
    )
  }
  roleDialogVisible.value = false
  ElMessage.success('角色分配成功')
  await loadUsers()
}

async function handleBatchStatus(isActive: boolean) {
  await batchSetUserStatus(
    selectedRows.value.map((item) => item.id),
    isActive,
  )
  ElMessage.success('批量状态已更新')
  await loadUsers()
}

async function handleCreateUser() {
  await createUser(createForm)
  createDialogVisible.value = false
  createForm.username = ''
  createForm.email = ''
  createForm.password = ''
  ElMessage.success('用户创建成功')
  await loadUsers()
}

onMounted(async () => {
  await Promise.all([loadRoles(), loadUsers()])
})
</script>

<template>
  <div class="shell-page">
    <div class="section-header">
      <div class="section-header__title">
        <h1>用户管理</h1>
        <div class="section-header__desc">管理登录账号、角色与权限分配。</div>
      </div>
      <div class="toolbar__right">
        <el-button v-if="canWrite" type="primary" @click="createDialogVisible = true">新建用户</el-button>
      </div>
    </div>
    <section class="glass-panel filter-strip">
      <div class="toolbar">
        <div class="toolbar__left">
          <div class="user-search">
            <el-input
              v-model="query.q"
              class="user-search__input"
              placeholder="搜索用户名/邮箱"
              clearable
              @keydown.enter="!isImeComposing($event) && loadUsers()"
              @clear="loadUsers"
            />
            <el-button type="primary" class="user-search__button" @click="loadUsers">搜索</el-button>
          </div>
        </div>

        <div class="toolbar__right">
          <el-button v-if="canWrite" :disabled="!selectedRows.length" @click="handleBatchStatus(true)">
            批量启用
          </el-button>
          <el-button v-if="canWrite" :disabled="!selectedRows.length" @click="handleBatchStatus(false)">
            批量禁用
          </el-button>
          <el-button v-if="canWrite" :disabled="!selectedRows.length" @click="openRoleDialog()">
            批量分配角色
          </el-button>
        </div>
      </div>
    </section>

    <section class="glass-panel table-panel">
      <div v-if="loading && !users.length" class="ui-skeleton-table" aria-hidden="true">
        <div v-for="i in 5" :key="`user-sk-${i}`" class="ui-skeleton-table__row">
          <span class="ui-skeleton" style="width: 24px; height: 24px; border-radius: 7px"></span>
          <span class="ui-skeleton" style="width: 18%; height: 15px"></span>
          <span class="ui-skeleton" style="width: 24%; height: 14px"></span>
          <span class="ui-skeleton ui-skeleton--pill" style="width: 64px; height: 22px"></span>
          <span class="ui-skeleton ui-skeleton--pill" style="width: 52px; height: 22px"></span>
          <span class="ui-skeleton" style="margin-left: auto; width: 96px; height: 15px"></span>
        </div>
      </div>

      <div v-else-if="isMobile" class="mobile-record-list" v-loading="loading && Boolean(users.length)">
        <div v-if="!users.length" class="empty-state empty-state--compact">
          <div class="empty-state__title">暂无匹配用户</div>
          <div class="empty-state__desc">尝试调整搜索关键词，或点击右上角新建用户。</div>
        </div>
        <article v-for="row in users" :key="row.id" class="mobile-record-card">
          <header class="mobile-record-card__head">
            <div class="mobile-record-card__head-main">
              <el-checkbox
                v-if="canWrite"
                :model-value="isUserSelected(row.id)"
                :aria-label="`选择 ${row.username}`"
                @change="toggleUserSelected(row, Boolean($event))"
              />
              <span class="user-avatar" aria-hidden="true">{{ String(row.username || '?').slice(0, 1).toUpperCase() }}</span>
              <strong class="mobile-user-name">{{ row.username }}</strong>
            </div>
            <el-tag :type="row.is_active ? 'success' : 'danger'" round size="small">
              {{ row.is_active ? '启用' : '禁用' }}
            </el-tag>
          </header>
          <dl class="mobile-record-dl">
            <div class="mobile-record-dl__row">
              <dt>邮箱</dt>
              <dd :class="{ 'user-muted': !row.email }">{{ row.email || '—' }}</dd>
            </div>
            <div class="mobile-record-dl__row">
              <dt>角色</dt>
              <dd>
                <div v-if="row.roles?.length" class="tag-list mobile-role-tags">
                  <el-tag v-for="role in row.roles" :key="role.id" type="info" round size="small">{{ role.name }}</el-tag>
                </div>
                <span v-else class="user-muted">—</span>
              </dd>
            </div>
          </dl>
          <footer v-if="canWrite" class="mobile-record-card__actions">
            <el-button link type="primary" @click="toggleStatus(row, !row.is_active)">
              {{ row.is_active ? '禁用' : '启用' }}
            </el-button>
            <el-button link type="primary" @click="openRoleDialog(row)">分配角色</el-button>
          </footer>
        </article>
      </div>

      <el-table v-else :data="users" border v-loading="loading && Boolean(users.length)" @selection-change="handleSelectionChange">
        <template #empty>
          <div class="empty-state empty-state--compact">
            <div class="empty-state__title">暂无匹配用户</div>
            <div class="empty-state__desc">尝试调整搜索关键词，或点击右上角新建用户。</div>
          </div>
        </template>
        <el-table-column type="selection" width="48" />
        <el-table-column label="用户名" min-width="160">
          <template #default="{ row }">
            <div class="user-name-cell">
              <span class="user-avatar" aria-hidden="true">{{ String(row.username || '?').slice(0, 1).toUpperCase() }}</span>
              <span>{{ row.username }}</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="邮箱" min-width="220">
          <template #default="{ row }">
            <span :class="{ 'user-muted': !row.email }">{{ row.email || '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="角色" min-width="200">
          <template #default="{ row }">
            <div v-if="row.roles?.length" class="tag-list">
              <el-tag v-for="role in row.roles" :key="role.id" type="info" round>{{ role.name }}</el-tag>
            </div>
            <span v-else class="user-muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="110">
          <template #default="{ row }">
            <el-tag :type="row.is_active ? 'success' : 'danger'" round>
              {{ row.is_active ? '启用' : '禁用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column
          v-if="canWrite"
          label="操作"
          :width="isMobile ? 124 : 200"
          class-name="user-op-column"
          label-class-name="user-op-column"
        >
          <template #default="{ row }">
            <div class="user-op">
              <el-button link type="primary" @click="toggleStatus(row, !row.is_active)">
                {{ row.is_active ? '禁用' : '启用' }}
              </el-button>
              <el-button link type="primary" @click="openRoleDialog(row)">分配角色</el-button>
            </div>
          </template>
        </el-table-column>
      </el-table>

      <div class="pagination-bar">
        <ResponsivePagination
          :current-page="query.page"
          :page-size="query.page_size"
          :total="total"
          label="个用户"
          :mobile="isMobile"
          :disabled="loading"
          @current-change="(v) => ((query.page = v), loadUsers())"
        >
          <template #desktop>
            <el-pagination
              v-model:current-page="query.page"
              v-model:page-size="query.page_size"
              background
              layout="total, sizes, prev, pager, next"
              :total="total"
              @change="loadUsers"
            />
          </template>
        </ResponsivePagination>
      </div>
    </section>

    <RoleAssignDialog
      v-model="roleDialogVisible"
      :roles="roles"
      :selected-role-ids="currentRoleIds"
      :title="roleDialogTitle"
      @submit="submitRoleDialog"
    />

    <el-dialog v-model="createDialogVisible" title="创建用户" width="420px">
      <el-form label-position="top">
        <el-form-item label="用户名">
          <el-input v-model="createForm.username" name="username" autocomplete="username" spellcheck="false" autocapitalize="off" />
        </el-form-item>
        <el-form-item label="邮箱">
          <el-input v-model="createForm.email" name="email" type="email" autocomplete="email" spellcheck="false" />
        </el-form-item>
        <el-form-item label="密码">
          <el-input v-model="createForm.password" name="password" type="password" autocomplete="new-password" show-password />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createDialogVisible = false">取消</el-button>
        <el-button type="primary" @click="handleCreateUser">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.table-panel {
  padding: 18px;
}

.user-name-cell {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.user-avatar {
  flex: 0 0 auto;
  width: 24px;
  height: 24px;
  border-radius: 7px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: var(--brand-soft);
  color: var(--brand);
  font-size: 12px;
  font-weight: 700;
}

.user-muted {
  color: var(--ink-muted);
}

.user-search {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.user-search__input {
  width: 260px;
}

.user-op {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.user-op :deep(.el-button + .el-button) {
  margin-left: 0;
}

@media (max-width: 767px) {
  .table-panel {
    padding: 14px;
  }

  .mobile-user-name {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: 14px;
    color: var(--ink);
  }

  .mobile-role-tags {
    justify-content: flex-end;
  }

  .toolbar__left {
    flex: 1 1 100%;
  }

  .user-search {
    width: 100%;
  }

  .user-search__input {
    flex: 1 1 0;
    width: auto;
    min-width: 0;
  }

  .user-search__button {
    flex: 0 0 auto;
  }

  .user-op {
    flex-wrap: nowrap;
    gap: 6px;
  }

  :deep(.user-op-column .cell) {
    padding: 0 8px;
  }
}
</style>
