import type { Plugin } from 'vue'
import { isImeComposing } from '@/utils/ime'

/**
 * 全局插件：单行输入框按回车时自动失焦（跳过 IME 组合键、多行文本框及原生提交表单）。
 */
export const inputBlurOnEnterPlugin: Plugin = {
  install() {
    document.addEventListener('keydown', (e: KeyboardEvent) => {
      if (e.key !== 'Enter' || isImeComposing(e)) return

      const target = e.target as HTMLElement | null
      if (!target) return

      const tagName = target.tagName.toLowerCase()
      if (tagName === 'textarea' || target.classList.contains('el-textarea__inner')) {
        return
      }

      const isSingleInput = tagName === 'input' || target.classList.contains('el-input__inner')
      if (!isSingleInput) return

      const form = target.closest('form')
      if (form?.querySelector('button[type="submit"], input[type="submit"]')) {
        return
      }

      target.blur()
    })
  },
}
