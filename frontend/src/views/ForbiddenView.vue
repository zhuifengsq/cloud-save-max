<script setup lang="ts">
import { useRouter } from 'vue-router'

const router = useRouter()
</script>

<template>
  <div class="simple-page forbidden-page">
    <section class="glass-panel forbidden-card">
      <el-result icon="warning" title="403 无权限" sub-title="当前账号缺少访问此页面所需的权限。">
        <template #extra>
          <div class="forbidden-actions">
            <el-button @click="router.back()">返回上一页</el-button>
            <el-button type="primary" @click="router.push('/')">返回首页</el-button>
          </div>
        </template>
      </el-result>
    </section>
  </div>
</template>

<style scoped>
.forbidden-page {
  min-height: 70vh;
  display: grid;
  place-items: center;
  padding: 24px 16px;
}

.forbidden-card {
  width: 100%;
  max-width: 480px;
  padding: 28px 24px;
}

.forbidden-actions {
  display: inline-flex;
  gap: 10px;
  flex-wrap: wrap;
  justify-content: center;
}
</style>
