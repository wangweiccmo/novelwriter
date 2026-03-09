import { type ReactNode } from 'react'
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { GenerationResults } from '@/pages/GenerationResults'

const createChapter = vi.fn()

vi.mock('@/services/api', () => ({
  api: {
    createChapter: (...args: unknown[]) => createChapter(...args),
    getContinuations: vi.fn(),
    submitFeedback: vi.fn(),
  },
  streamContinuation: vi.fn(),
  ApiError: class ApiError extends Error {
    status: number

    constructor(status: number, message: string) {
      super(message)
      this.status = status
    }
  },
}))

vi.mock('@/contexts/AuthContext', () => ({
  useAuth: () => ({ user: { feedback_submitted: true }, refreshQuota: vi.fn() }),
}))

vi.mock('@/components/layout/PageShell', () => ({
  PageShell: ({ children }: { children: ReactNode }) => <div>{children}</div>,
}))

vi.mock('@/components/GlassCard', () => ({
  GlassCard: ({ children }: { children: ReactNode }) => <div>{children}</div>,
}))

vi.mock('@/components/ui/nw-button', () => ({
  NwButton: ({ children, ...props }: { children: ReactNode } & React.ButtonHTMLAttributes<HTMLButtonElement>) => (
    <button type="button" {...props}>{children}</button>
  ),
}))

vi.mock('@/components/ui/plain-text-content', () => ({
  PlainTextContent: ({ content, emptyLabel }: { content?: string; emptyLabel?: string }) => (
    <div>{content || emptyLabel || ''}</div>
  ),
}))

vi.mock('@/components/workspace/InjectionSummaryModal', () => ({
  InjectionSummaryModal: () => null,
}))

vi.mock('@/components/feedback/FeedbackForm', () => ({
  FeedbackForm: () => null,
}))

function renderPage(state: unknown) {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  })

  return render(
    <MemoryRouter
      initialEntries={[
        { pathname: '/novel/1/chapter/3/results', state },
      ]}
    >
      <QueryClientProvider client={client}>
        <Routes>
          <Route path="/novel/:novelId/chapter/:chapterNum/results" element={<GenerationResults />} />
          <Route path="/novel/:novelId" element={<div>novel-page</div>} />
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>,
  )
}

describe('GenerationResults', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    createChapter.mockResolvedValue({
      id: 100,
      novel_id: 1,
      chapter_number: 4,
      version_number: 2,
      version_count: 2,
      title: '',
      content: 'version-A',
      created_at: '2026-03-09T00:00:00Z',
      updated_at: null,
    })
  })

  it('adopts continuation as next chapter version anchored by route chapter number', async () => {
    renderPage({
      response: {
        continuations: [
          {
            id: 11,
            novel_id: 1,
            chapter_number: 4,
            content: 'version-A',
            rating: null,
            created_at: '2026-03-09T00:00:00Z',
          },
        ],
      },
    })

    const adoptButton = await screen.findByTestId('results-adopt-button')
    await userEvent.click(adoptButton)

    await waitFor(() => {
      expect(createChapter).toHaveBeenCalledWith(1, {
        content: 'version-A',
        after_chapter_number: 3,
      })
    })
  })
})
