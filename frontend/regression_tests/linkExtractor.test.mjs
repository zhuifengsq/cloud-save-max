import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import ts from 'typescript'

const source = readFileSync(new URL('../src/utils/linkExtractor.ts', import.meta.url), 'utf8')
const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } })
const { extractTable, filterQuark, organizeDualDisk, generateHotUpdates, parseWpsHeadline } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)

test('table extracts a title with multiple drives and strips year and progress', () => {
  assert.deepEqual(extractTable('尚公主 (2026) 【更23集】\n百度链接：https://pan.baidu.com/s/abc?pwd=1111\n夸克链接：https://pan.quark.cn/s/def'), [
    { title: '尚公主', url: 'https://pan.baidu.com/s/abc?pwd=1111' },
    { title: '尚公主', url: 'https://pan.quark.cn/s/def' },
  ])
})

test('table accepts inline titles and reports unnamed URLs without inventing a title', () => {
  assert.deepEqual(extractTable('https://pan.quark.cn/s/a\n小城良方（2026）【更18集】 链接：https://pan.quark.cn/s/b'), [
    { title: '未匹配到影视名称', url: 'https://pan.quark.cn/s/a' },
    { title: '小城良方', url: 'https://pan.quark.cn/s/b' },
  ])
})

test('table strips share boilerplate without treating passcodes as titles', () => {
  assert.deepEqual(extractTable('我用夸克网盘分享了「嫁金钗」\n链接：https://pan.quark.cn/s/a\n提取码：1111\nhttps://pan.baidu.com/s/b'), [
    { title: '嫁金钗', url: 'https://pan.quark.cn/s/a' },
    { title: '嫁金钗', url: 'https://pan.baidu.com/s/b' },
  ])
})

test('quark filtering keeps grouped titles and excludes other drives', () => {
  const text = '剧一\n百度链接：https://pan.baidu.com/s/a\n夸克链接：https://pan.quark.cn/s/b\n\n剧二\n迅雷链接：https://pan.xunlei.com/s/c\n\n剧三\n夸克链接：https://pan.quark.cn/s/d'
  assert.equal(filterQuark(text, '自定义后缀'), '剧一\n夸克链接：https://pan.quark.cn/s/b\n\n剧三\n夸克链接：https://pan.quark.cn/s/d\n\n自定义后缀')
})

test('dual disk handles Markdown links, punctuation, deduplication and drive ordering', () => {
  const result = organizeDualDisk('尚公主\n[夸克](https://pan.quark.cn/s/a)\n百度：https://pan.baidu.com/s/b?pwd=1111。\nhttps://pan.quark.cn/s/a\nhttps://pan.quark.cn.evil.test/s/c')
  assert.equal(result.text, '尚公主\n百度链接：https://pan.baidu.com/s/b?pwd=1111\n夸克链接：https://pan.quark.cn/s/a')
  assert.equal(result.baiduCount, 1)
  assert.equal(result.quarkCount, 1)
})

test('hot updates remove links and years, deduplicate, keep prefix and suffix', () => {
  const result = generateHotUpdates('尚公主 (2026) 【更23集】\nhttps://pan.quark.cn/s/a\n尚公主 (2026) 【更23集】\n小城良方【更18集】', '今日更新', '汇总地址')
  assert.equal(result.count, 2)
  assert.equal(result.text, '今日更新\n\n尚公主【更23集】\n小城良方【更18集】\n汇总地址')
})

test('decimal variety-show episode numbers are recognized in table and hot updates', () => {
  assert.deepEqual(parseWpsHeadline('🔥「我家那闺女 更11.8期」4K'), { title: '我家那闺女', episodeText: '更11.8期' })
  assert.deepEqual(extractTable('🔥「我家那闺女 更11.8期」4K\n百度链接：https://pan.baidu.com/s/a'), [
    { title: '我家那闺女', url: 'https://pan.baidu.com/s/a' },
  ])
  const hot = generateHotUpdates('🔥「我家那闺女 更11.8期」4K', '', '')
  assert.equal(hot.count, 1)
  assert.equal(hot.text, '我家那闺女【更11.8期】')
})

test('empty and unrelated input produces no fabricated resources', () => {
  assert.deepEqual(extractTable('只有文字'), [])
  assert.equal(organizeDualDisk('').baiduCount, 0)
  assert.equal(generateHotUpdates('没有剧集信息', '', '').count, 0)
  assert.equal(filterQuark('', ''), '')
})
