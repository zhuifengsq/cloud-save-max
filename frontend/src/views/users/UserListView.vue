<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { computed, onMounted, reactive, ref } from 'vue'

import RoleAssignDialog from '@/components/users/RoleAssignDialog.vue'
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
  } finally {
    loading.value = false
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
  <div class="page-stack">
    <div class="section-header">
            <div class="section-header__title">
        <h2>用户管理</h2>
        <div class="section-header__desc">管理登录账号、角色与权限分配。</div>
      </div>
    </div>
    <el-card shadow="never">
      <div class="toolbar">
        <div class="toolbar__left">
          <div class="user-search">
            <el-input
              v-model="query.q"
              class="user-search__input"
              placeholder="搜索用户名/邮箱"
              clearable
              @keyup.enter="loadUsers"
              @clear="loadUsers"
            />
            <el-button type="primary" class="user-search__button" @click="loadUsers">搜索</el-button>
          </div>
        </div>

        <div class="toolbar__right">
          <el-button v-if="canWrite" type="success" @click="createDialogVisible = true">新建用户</el-button>
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
    </el-card>

    <el-card shadow="never">
      <el-table :data="users" border v-loading="loading" @selection-change="handleSelectionChange">
        <el-table-column type="selection" width="48" />
        <el-table-column prop="username" label="用户名" min-width="140" />
        <el-table-column prop="email" label="邮箱" min-width="220" />
        <el-table-column label="角色" min-width="220">
          <template #default="{ row }">
            <div class="tag-list">
              <el-tag v-for="role in row.roles" :key="role.id" type="info">{{ role.name }}</el-tag>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="120">
          <template #default="{ row }">
            <el-tag :type="row.is_active ? 'success' : 'danger'">
              {{ row.is_active ? '启用' : '禁用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column
          v-if="canWrite"
          label="操作"
          :width="isMobile ? 124 : 240"
          fixed="right"
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
        <el-pagination
          v-model:current-page="query.page"
          v-model:page-size="query.page_size"
          background
          layout="total, sizes, prev, pager, next"
          :total="total"
          @change="loadUsers"
        />
      </div>
    </el-card>

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
          <el-input v-model="createForm.username" />
        </el-form-item>
        <el-form-item label="邮箱">
          <el-input v-model="createForm.email" />
        </el-form-item>
        <el-form-item label="密码">
          <el-input v-model="createForm.password" type="password" show-password />
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
