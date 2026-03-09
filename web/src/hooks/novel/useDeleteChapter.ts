import { useMutation, useQueryClient } from "@tanstack/react-query"
import { api } from "@/services/api"
import { novelKeys } from "@/hooks/novel/keys"

type DeleteChapterInput = {
  chapterNum: number
  version?: number | null
}

export function useDeleteChapter(novelId: number) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ chapterNum, version }: DeleteChapterInput) =>
      api.deleteChapter(novelId, chapterNum, version ?? undefined),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: novelKeys.chaptersMeta(novelId) })
      qc.invalidateQueries({ queryKey: novelKeys.detail(novelId) })
    },
  })
}
