import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '@/services/api'
import { novelKeys } from '@/hooks/novel/keys'
import type { Chapter, ChapterMeta, ChapterUpdateRequest } from '@/types/api'

function applyChapterPatch(prev: Chapter, patch: ChapterUpdateRequest): Chapter {
  const next = { ...prev }
  if (patch.title !== undefined) next.title = patch.title
  if (patch.content !== undefined) next.content = patch.content
  return next
}

export function useUpdateChapter(novelId: number, chapterNum: number, version?: number | null) {
  const qc = useQueryClient()
  const chapterKey = novelKeys.chapter(novelId, chapterNum, version)
  return useMutation({
    mutationFn: (data: ChapterUpdateRequest) => api.updateChapter(novelId, chapterNum, data, version ?? undefined),
    onMutate: async (patch) => {
      await qc.cancelQueries({ queryKey: chapterKey })
      await qc.cancelQueries({ queryKey: novelKeys.chaptersMeta(novelId) })

      const previousChapter = qc.getQueryData<Chapter>(chapterKey)
      const previousMeta = qc.getQueryData<ChapterMeta[]>(novelKeys.chaptersMeta(novelId))

      if (previousChapter) {
        qc.setQueryData<Chapter>(chapterKey, applyChapterPatch(previousChapter, patch))
      }

      if (previousMeta && patch.title !== undefined) {
        const requestedVersion = version ?? null
        qc.setQueryData<ChapterMeta[]>(
          novelKeys.chaptersMeta(novelId),
          previousMeta.map((m) => {
            if (m.chapter_number !== chapterNum) return m
            const shouldPatchMetaTitle = requestedVersion === null || m.latest_version_number === requestedVersion
            return shouldPatchMetaTitle ? { ...m, title: patch.title ?? '' } : m
          }),
        )
      }

      return { previousChapter, previousMeta }
    },
    onError: (_err, _patch, context) => {
      if (context?.previousChapter) {
        qc.setQueryData(chapterKey, context.previousChapter)
      }
      if (context?.previousMeta) {
        qc.setQueryData(novelKeys.chaptersMeta(novelId), context.previousMeta)
      }
    },
    onSuccess: (updated) => {
      qc.setQueryData<Chapter>(chapterKey, updated)
      qc.setQueryData<ChapterMeta[]>(novelKeys.chaptersMeta(novelId), (old) => {
        if (!old) return old
        return old.map((m) => {
          if (m.chapter_number !== chapterNum) return m
          const shouldPatchMetaTitle = m.latest_version_number === updated.version_number
          return shouldPatchMetaTitle ? { ...m, title: updated.title } : m
        })
      })
    },
  })
}
