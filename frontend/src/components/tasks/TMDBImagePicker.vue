<script setup lang="ts">
import {computed, reactive, ref, watch} from 'vue'
import {fetchTMDBImages} from '@/api/media'
import type {TMDBImage, TMDBImages} from '@/types/media'

const props = defineProps<{
  modelValue: boolean
  mediaType: 'movie' | 'tv'
  tmdbId: number | null
  selectedPath: string
}>()
const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  select: [image: TMDBImage, kind: keyof TMDBImages]
}>()

const gallery = reactive({loading: false, error: '', images: {posters: [], backdrops: []} as TMDBImages})
const activeType = ref<keyof TMDBImages>('posters')
const imagePage = ref(1)
const pageSize = 24
const currentImages = computed(() => gallery.images[activeType.value])
const visibleImages = computed(() => currentImages.value.slice((imagePage.value - 1) * pageSize, imagePage.value * pageSize))
let loadedKey = ''
let loadedAt = 0
let requestKey = ''
let requestVersion = 0

async function loadImages(force = false) {
  const id = props.tmdbId
  if (!id) return
  const key = `${props.mediaType}:${id}`
  if (!force && loadedKey === key && Date.now() - loadedAt < 5 * 60_000) return
  if (!force && gallery.loading && requestKey === key) return
  const version = ++requestVersion
  requestKey = key
  gallery.loading = true
  gallery.error = ''
  if (loadedKey !== key) gallery.images = {posters: [], backdrops: []}
  try {
    const images = await fetchTMDBImages(props.mediaType, id)
    if (version !== requestVersion || key !== `${props.mediaType}:${props.tmdbId}`) return
    gallery.images = images
    loadedKey = key
    loadedAt = Date.now()
    imagePage.value = 1
    if (!images[activeType.value].length) activeType.value = images.posters.length ? 'posters' : 'backdrops'
  } catch (error: any) {
    if (version !== requestVersion || key !== `${props.mediaType}:${props.tmdbId}`) return
    gallery.error = error?.response?.data?.message || '配图获取失败，请重试'
  } finally {
    if (version === requestVersion) gallery.loading = false
  }
}

function chooseImage(image: TMDBImage) {
  emit('select', image, activeType.value)
  emit('update:modelValue', false)
}

function languageLabel(language: string | null) {
  if (!language) return '无文字'
  return ({zh: '中文', en: '英文', ja: '日文', ko: '韩文'} as Record<string, string>)[language] || language.toUpperCase()
}

watch(() => [props.modelValue, props.mediaType, props.tmdbId], () => {
  if (props.modelValue) void loadImages()
}, {immediate: true})
watch(activeType, () => {imagePage.value = 1})
</script>

<template>
  <el-dialog
    :model-value="modelValue" title="选择更多配图" width="920px"
    style="max-width: 94vw" append-to-body class="image-picker-dialog"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <div class="image-picker__toolbar">
      <el-radio-group v-model="activeType" size="small" aria-label="配图类型">
        <el-radio-button value="posters">海报（{{ gallery.images.posters.length }}）</el-radio-button>
        <el-radio-button value="backdrops">横版配图（{{ gallery.images.backdrops.length }}）</el-radio-button>
      </el-radio-group>
      <el-button text :disabled="gallery.loading" @click="loadImages(true)">刷新列表</el-button>
    </div>
    <p class="image-picker__hint">选择一张图片，用于当前文案的预览、复制和下载。</p>
    <div v-if="gallery.loading" class="image-picker__loading" v-loading="true" aria-label="正在加载配图" />
    <div v-else-if="gallery.error" class="image-picker__error" role="alert">
      <el-alert :title="gallery.error" type="error" :closable="false" />
      <el-button @click="loadImages(true)">重试</el-button>
    </div>
    <el-empty v-else-if="!currentImages.length" :description="gallery.images.posters.length || gallery.images.backdrops.length ? '此分类暂无配图' : '暂无更多配图'" />
    <template v-else>
      <div class="image-picker__grid" :class="{'image-picker__grid--wide': activeType === 'backdrops'}">
        <button
          v-for="(image, index) in visibleImages" :key="image.file_path" type="button"
          class="image-picker__item" :class="{'is-selected': selectedPath === image.file_path}"
          :aria-label="`选择${activeType === 'posters' ? '海报' : '横版配图'} ${(imagePage - 1) * pageSize + index + 1}`"
          :aria-pressed="selectedPath === image.file_path" @click="chooseImage(image)"
        >
          <el-image :src="`https://image.tmdb.org/t/p/w300${image.file_path}`" fit="contain" loading="lazy">
            <template #error><span class="image-picker__broken">图片加载失败</span></template>
          </el-image>
          <span class="image-picker__caption">
            <span>{{ selectedPath === image.file_path ? '已选择' : languageLabel(image.language) }}</span>
            <span>{{ image.width }} × {{ image.height }}</span>
          </span>
        </button>
      </div>
      <el-pagination
        v-if="currentImages.length > pageSize" v-model:current-page="imagePage" :page-size="pageSize"
        :total="currentImages.length" layout="prev, pager, next" :pager-count="5" size="small" class="image-picker__pagination"
      />
    </template>
    <template #footer><el-button @click="emit('update:modelValue', false)">取消</el-button></template>
  </el-dialog>
</template>

<style scoped>
.image-picker__toolbar {display: flex; justify-content: space-between; align-items: center; gap: 8px; flex-wrap: wrap;}
.image-picker__hint {color: var(--el-text-color-secondary); font-size: 13px; margin: 12px 0 16px;}
.image-picker__loading {min-height: 240px;}
.image-picker__error {display: grid; justify-items: start; gap: 16px; min-height: 160px;}
.image-picker__grid {display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; max-height: 58vh; overflow-y: auto; padding: 3px;}
.image-picker__grid--wide {grid-template-columns: repeat(3, minmax(0, 1fr));}
.image-picker__item {display: flex; flex-direction: column; min-width: 0; padding: 0; border: 2px solid transparent; border-radius: 8px; overflow: hidden; background: var(--el-fill-color-light); color: var(--el-text-color-regular); cursor: pointer;}
.image-picker__item:hover, .image-picker__item:focus-visible, .image-picker__item.is-selected {border-color: var(--el-color-primary);}
.image-picker__item .el-image {width: 100%; aspect-ratio: 2 / 3; display: block;}
.image-picker__grid--wide .el-image {aspect-ratio: 16 / 9;}
.image-picker__caption {display: flex; justify-content: space-between; gap: 4px; padding: 8px; width: 100%; box-sizing: border-box; font-size: 12px; flex-wrap: wrap;}
.image-picker__broken {font-size: 12px; color: var(--el-text-color-secondary);}
.image-picker__pagination {justify-content: center; margin-top: 16px;}
@media (max-width: 600px) {
  .image-picker__grid, .image-picker__grid--wide {grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px;}
}
</style>
