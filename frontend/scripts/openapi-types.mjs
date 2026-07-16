import { spawnSync } from 'node:child_process'
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const scriptDir = dirname(fileURLToPath(import.meta.url))
const frontendDir = resolve(scriptDir, '..')
const rootDir = resolve(frontendDir, '..')
const backendDir = join(rootDir, 'backend')
const buildDir = join(rootDir, '.build')
const openapiPath = join(buildDir, 'openapi.json')
const generatedPath = join(frontendDir, 'src', 'api', 'generated', 'schema.d.ts')
const temporaryPath = join(buildDir, 'schema.generated.d.ts')
const checkOnly = process.argv.includes('--check')

const pythonCandidates = process.platform === 'win32'
  ? [
      join(backendDir, '.venv', 'Scripts', 'python.exe'),
      'python',
      'py',
    ]
  : [
      join(backendDir, '.venv', 'bin', 'python'),
      'python3',
      'python',
    ]

function run(command, args, cwd = rootDir) {
  const result = spawnSync(command, args, {
    cwd,
    encoding: 'utf8',
    stdio: 'inherit',
  })
  if (result.error || result.status !== 0) {
    return false
  }
  return true
}

mkdirSync(buildDir, { recursive: true })
let exported = false
for (const python of pythonCandidates) {
  if (python.includes('\\') || python.includes('/')) {
    if (!existsSync(python)) continue
  }
  const args = python === 'py'
    ? ['-3', 'scripts/export_openapi.py', openapiPath]
    : ['scripts/export_openapi.py', openapiPath]
  if (run(python, args, backendDir)) {
    exported = true
    break
  }
}
if (!exported) {
  throw new Error('Unable to export OpenAPI. Install backend dependencies first.')
}

const openapiCli = join(
  frontendDir,
  'node_modules',
  'openapi-typescript',
  'bin',
  'cli.js',
)
if (!run(process.execPath, [openapiCli, openapiPath, '--output', temporaryPath], frontendDir)) {
  throw new Error('openapi-typescript failed.')
}

const generated = readFileSync(temporaryPath, 'utf8').replaceAll('\r\n', '\n')
rmSync(temporaryPath, { force: true })
if (checkOnly) {
  if (!existsSync(generatedPath)) {
    throw new Error('Generated OpenAPI types are missing. Run npm run api:generate.')
  }
  const existing = readFileSync(generatedPath, 'utf8').replaceAll('\r\n', '\n')
  if (existing !== generated) {
    throw new Error('Generated OpenAPI types are stale. Run npm run api:generate.')
  }
  console.log('OpenAPI types are current.')
} else {
  mkdirSync(dirname(generatedPath), { recursive: true })
  writeFileSync(generatedPath, generated, 'utf8')
  console.log(`Generated ${generatedPath}`)
}
