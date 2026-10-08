import { BarChart, LineChart, PieChart } from 'echarts/charts'
import {
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  MarkAreaComponent,
  MarkLineComponent,
  TooltipComponent,
} from 'echarts/components'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import VChart from 'vue-echarts'

use([
  LineChart, BarChart, PieChart, GridComponent, TooltipComponent, LegendComponent,
  MarkLineComponent, MarkAreaComponent, DataZoomComponent, CanvasRenderer,
])

export { VChart }

/** Shared dark-theme defaults so every chart looks like the rest of the UI. */
export const AXIS = {
  axisLine: { lineStyle: { color: '#24324b' } },
  axisLabel: { color: '#8a9ab5', fontSize: 11 },
  splitLine: { lineStyle: { color: '#1a2438' } },
}
export const TOOLTIP = {
  trigger: 'axis' as const,
  backgroundColor: '#172238',
  borderColor: '#24324b',
  textStyle: { color: '#e2e8f0', fontSize: 12 },
}
/** Categorical slots in fixed order (validated for the dark surface #111a2c: CVD ΔE ≥ 9.4, contrast ≥ 3:1). */
export const PALETTE = ['#3987e5', '#d95926', '#199e70', '#c98500', '#d55181', '#9085e9']
/** Status colours are reserved for state and always shipped with a label. */
export const STATUS = { info: '#3987e5', warning: '#fab219', critical: '#d03b3b' } as const
export const SURFACE = '#111a2c'
