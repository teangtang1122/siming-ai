import { readFile, readdir } from 'node:fs/promises'
import { join } from 'node:path'
import process from 'node:process'

const frontendRoot = process.cwd()
const pagesRoot = join(frontendRoot, 'src', 'pages')
const baseline = JSON.parse(
  await readFile(join(frontendRoot, 'architecture-baseline.json'), 'utf8'),
)

const files = (await readdir(pagesRoot)).filter((name) => name.endsWith('.tsx'))
let apiImports = 0
let useEffectCalls = 0
const errors = []
const warnings = []

for (const file of files) {
  const source = await readFile(join(pagesRoot, file), 'utf8')
  if (/from ['"]\.\.\/(?:shared\/)?api\/client['"]/.test(source)) {
    apiImports += 1
  }
  useEffectCalls += source.match(/\buseEffect\s*\(/g)?.length || 0
  const lines = source.split(/\r?\n/).length
  const grandfathered = Number(baseline.oversized_pages[file] || 0)
  if (lines > 1000 && (!grandfathered || lines > grandfathered)) {
    errors.push(`${file} has ${lines} lines; split controller state from views`)
  } else if (lines > 600) {
    warnings.push(`${file} remains large at ${lines} lines`)
  }
}

const compare = (label, count, expected) => {
  if (count > expected) {
    errors.push(`${label} increased from ${expected} to ${count}`)
  } else if (count < expected) {
    warnings.push(`${label} improved from ${expected} to ${count}; lower the baseline`)
  }
}

compare('page apiClient imports', apiImports, baseline.legacy_page_api_imports)
compare('page useEffect calls', useEffectCalls, baseline.legacy_page_use_effect_calls)

for (const warning of warnings.sort()) console.warn(`FRONTEND-ARCH-WARN: ${warning}`)
for (const error of errors.sort()) console.error(`FRONTEND-ARCH-ERROR: ${error}`)
console.log(
  `Frontend boundary summary: pages=${files.length} api_imports=${apiImports} `
  + `use_effects=${useEffectCalls} errors=${errors.length}`,
)
if (errors.length) process.exit(1)
