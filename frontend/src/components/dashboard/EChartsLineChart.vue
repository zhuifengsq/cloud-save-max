<script setup lang="ts">
import type { ComposeOption, ECharts } from 'echarts/core'
import type { LineSeriesOption } from 'echarts/charts'
import type {
  GridComponentOption,
  LegendComponentOption,
  TooltipComponentOption,
} from 'echarts/components'

type ChartOption = ComposeOption<
  LineSeriesOption | GridComponentOption | LegendComponentOption | TooltipComponentOption
>

type AxisKey = 'left' | 'right'

type Series = {
  key: string
  name: string
  color: string
  axis: AxisKey
  values: Array<number | null>
  dashed?: boolean
}

const props = withDefaults(
  defineProps<{
    xLabels: string[]
    series: Series[]
    height?: number
  }>(),
  {
    height: 280,
  },
)

const rootEl = ref<HTMLDivElement | null>(null)
let echartsInit: typeof import('@/utils/echartsLine').init | null = null
let chart: ECharts | null = null
let resizeObserver: ResizeObserver | null = null
let themeListener: ((e: Event) => void) | null = null

async function getEchartsInit() {
  if (!echartsInit) {
    const mod = await import('@/utils/echartsLine')
    echartsInit = mod.init
  }
  return echartsInit
}

function buildOption(): ChartOption {
  const cssVar = (name: string, fallback: string) => {
    if (typeof window === 'undefined') return fallback
    const value = window.getComputedStyle(document.documentElement).getPropertyValue(name).trim()
    return value || fallback
  }

  const textPrimary = cssVar('--el-text-color-primary', '#0f172a')
  const textSecondary = cssVar('--el-text-color-secondary', '#64748b')
  const border = cssVar('--el-border-color', 'rgba(148,163,184,0.35)')
  const borderLight = cssVar('--el-border-color-lighter', 'rgba(148,163,184,0.18)')
  const overlayBg = cssVar('--el-bg-color-overlay', 'rgba(255,255,255,0.92)')

  const leftSeries = props.series.filter((s) => s.axis === 'left')
  const rightSeries = props.series.filter((s) => s.axis === 'right')

  const option: ChartOption = {
    grid: { left: 44, right: 54, top: 38, bottom: 34, containLabel: false },
    tooltip: {
      trigger: 'axis',
      axisPointer: {
        type: 'line',
        lineStyle: { color: border },
        label: { backgroundColor: overlayBg, color: textPrimary, borderColor: borderLight },
      },
      backgroundColor: overlayBg,
      borderColor: borderLight,
      textStyle: { color: textPrimary },
      valueFormatter: (value: any) => {
        const n = Number(value)
        if (!Number.isFinite(n)) return '--'
        return String(n)
      },
    },
    legend: {
      top: 0,
      left: 0,
      icon: 'circle',
      itemWidth: 10,
      itemHeight: 10,
      textStyle: { color: textSecondary },
    },
    xAxis: {
      type: 'category',
      boundaryGap: false,
      data: props.xLabels,
      axisLabel: { color: textSecondary },
      axisLine: { lineStyle: { color: border } },
      axisTick: { show: false },
    },
    yAxis: [
      {
        type: 'value',
        name: '',
        axisLabel: { color: textSecondary },
        splitLine: { lineStyle: { color: borderLight } },
      },
      {
        type: 'value',
        min: 0,
        max: 1,
        axisLabel: { color: textSecondary, formatter: (v: number) => `${Math.round(v * 100)}%` },
        splitLine: { show: false },
      },
    ],
    series: [
      ...leftSeries.map((s) => ({
        name: s.name,
        type: 'line' as const,
        yAxisIndex: 0,
        smooth: true,
        showSymbol: false,
        emphasis: { focus: 'series' as const },
        data: s.values,
        lineStyle: { color: s.color, width: 2.6, type: (s.dashed ? 'dashed' : 'solid') as 'solid' | 'dashed' },
        itemStyle: { color: s.color },
        ...(s.dashed ? {} : { areaStyle: { opacity: 0.14 } }),
      })),
      ...rightSeries.map((s) => ({
        name: s.name,
        type: 'line' as const,
        yAxisIndex: 1,
        smooth: true,
        showSymbol: false,
        emphasis: { focus: 'series' as const },
        data: s.values,
        tooltip: {
          valueFormatter: (value: any) => {
            const n = Number(value)
            if (!Number.isFinite(n)) return '--'
            return `${(n * 100).toFixed(n >= 0.1 ? 1 : 2)}%`
          },
        },
        lineStyle: { color: s.color, width: 2.6, type: (s.dashed ? 'dashed' : 'solid') as 'solid' | 'dashed' },
        itemStyle: { color: s.color },
      })),
    ],
    animation: false,
    color: props.series.map((s) => s.color),
    backgroundColor: 'transparent',
    textStyle: { color: textPrimary },
  }

  return option
}

async function ensureChart(retry = 0) {
  const el = rootEl.value
  if (!el) return
  const w = el.clientWidth
  const h = el.clientHeight
  if ((!w || !h) && retry < 10) {
    window.requestAnimationFrame(() => {
      ensureChart(retry + 1)
    })
    return
  }
  if (!chart) {
    const initChart = await getEchartsInit()
    chart = initChart(el, undefined, { renderer: 'canvas' })
  }
  chart.setOption(buildOption(), { notMerge: true })
}

onMounted(() => {
  ensureChart()
  themeListener = () => ensureChart()
  window.addEventListener('theme-change', themeListener)
  const el = rootEl.value
  if (!el) return
  resizeObserver = new ResizeObserver(() => {
    chart?.resize()
  })
  resizeObserver.observe(el)
})

watch(
  () => [props.xLabels, props.series],
  () => ensureChart(),
  { deep: true },
)

watch(
  () => props.height,
  () => nextTick(() => chart?.resize()),
)

onBeforeUnmount(() => {
  resizeObserver?.disconnect()
  resizeObserver = null
  if (themeListener) window.removeEventListener('theme-change', themeListener)
  themeListener = null
  chart?.dispose()
  chart = null
})
</script>

<template>
  <div class="echarts-wrap" :style="{ height: `${height}px` }">
    <div ref="rootEl" class="echarts-root"></div>
  </div>
</template>

<style scoped>
.echarts-wrap {
  width: 100%;
}

.echarts-root {
  width: 100%;
  height: 100%;
}
</style>
