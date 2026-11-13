#!/usr/bin/env node
// Mbiling: puts Git for Windows (portable) in build/portable-git, so the Mains have a bash on a
// PC without Git. electron-builder ships it as resources/git (see electron-builder.config.cjs).
//
// The download is the one pinned in pm/lock.json (packages.git, win32-x64): same URL and
// SHA-256 as the installers use. Nothing is extracted unless the checksum matches.
//
//   node scripts/fetch-portable-git.mjs        (Windows only; reruns are no-ops)
import { spawnSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const desktop = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const root = path.resolve(desktop, '..', '..')
const target = path.join(desktop, 'build', 'portable-git')
const cacheDir = path.join(desktop, 'build', 'cache')

if (process.platform !== 'win32') {
  console.log('[portable-git] skipped: Windows only')
  process.exit(0)
}

const lock = JSON.parse(fs.readFileSync(path.join(root, 'pm', 'lock.json'), 'utf8'))
const pin = lock.packages?.git?.artifacts?.[`win32-${process.arch}`]

if (!pin?.url || !pin?.sha256) {
  throw new Error(`[portable-git] no pin for win32-${process.arch} in pm/lock.json`)
}

const stamp = path.join(target, '.mbiling-pin')

if (fs.existsSync(stamp) && fs.readFileSync(stamp, 'utf8').trim() === pin.sha256) {
  console.log('[portable-git] already present')
  process.exit(0)
}

fs.mkdirSync(cacheDir, { recursive: true })
const archive = path.join(cacheDir, path.basename(new URL(pin.url).pathname))
const sha256 = file => createHash('sha256').update(fs.readFileSync(file)).digest('hex')

if (!fs.existsSync(archive) || sha256(archive) !== pin.sha256) {
  console.log(`[portable-git] downloading ${pin.url}`)
  const response = await fetch(pin.url)

  if (!response.ok) {
    throw new Error(`[portable-git] download failed: HTTP ${response.status}`)
  }

  fs.writeFileSync(archive, Buffer.from(await response.arrayBuffer()))
}

const actual = sha256(archive)

if (actual !== pin.sha256) {
  fs.rmSync(archive, { force: true })
  throw new Error(`[portable-git] checksum mismatch: expected ${pin.sha256}, got ${actual}`)
}

fs.rmSync(target, { recursive: true, force: true })
fs.mkdirSync(target, { recursive: true })
// PortableGit is a 7-Zip self-extractor: -o<dir> -y extracts silently.
const result = spawnSync(archive, [`-o${target}`, '-y'], { stdio: 'inherit', windowsHide: true })

if (result.status !== 0 || !fs.existsSync(path.join(target, 'bin', 'bash.exe'))) {
  throw new Error(`[portable-git] extraction failed (status ${result.status})`)
}

fs.writeFileSync(stamp, `${pin.sha256}\n`)
console.log(`[portable-git] ready in ${target}`)
