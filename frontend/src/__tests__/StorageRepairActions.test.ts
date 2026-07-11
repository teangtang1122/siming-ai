import { describe, expect, it } from 'vitest'
import { findStorageHealth } from '../components/StorageRepairActions'

describe('findStorageHealth', () => {
  it('finds nested orphan chapter files from run payloads', () => {
    const health = findStorageHealth({
      validation: {
        storage_health: {
          storage_target: 'database_authoritative',
          orphan_chapter_file_count: 1,
          orphan_chapter_files: [
            { path: 'chapters/0151-direct-cli.md', title: '第151章 抢网', word_count: 1200 },
          ],
          next_action: "sync_project_files(direction='import', confirm_import_from_files=true)",
        },
      },
    })

    expect(health?.orphan_chapter_file_count).toBe(1)
    expect(health?.orphan_chapter_files?.[0].path).toBe('chapters/0151-direct-cli.md')
  })
})
