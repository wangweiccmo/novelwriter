// SPDX-FileCopyrightText: 2026 Isaac.X.Ω.Yuan
// SPDX-License-Identifier: AGPL-3.0-only

import { useState, useEffect, useCallback, useRef } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { ArrowLeft, Sparkles, ChevronDown, ChevronUp } from 'lucide-react'
import { GlassCard } from '@/components/GlassCard'
import { AdvancedRow } from '@/components/workspace/AdvancedRow'
import { PageShell } from '@/components/layout/PageShell'
import { NwButton } from '@/components/ui/nw-button'
import { Textarea } from '@/components/ui/textarea'
import { Input } from '@/components/ui/input'
import { Checkbox } from '@/components/ui/checkbox'
import { PlainTextContent } from '@/components/ui/plain-text-content'
import { cn } from '@/lib/utils'
import { api } from '@/services/api'
import { useAuth } from '@/contexts/AuthContext'
import type { Chapter } from '@/types/api'


type LengthOption = {
  label: string
  value: string
  disabled: boolean
}

const LENGTH_OPTIONS: LengthOption[] = [
  { label: '2000', value: '2000', disabled: false },
  { label: '3000', value: '3000', disabled: false },
  { label: '4000', value: '4000', disabled: false },
]

const MIN_CONTEXT_CHAPTERS = 1
const MAX_CONTEXT_CHAPTERS = 5
const DEFAULT_CONTEXT_CHAPTERS = 5
const MIN_TARGET_CHARS = 800
const MAX_TARGET_CHARS = 8000
const DEFAULT_TARGET_CHARS = 3000
const MAX_NUM_VERSIONS = 4

const DEMO_NOVEL_TITLE = '西游记'
const DEMO_DEFAULT_INSTRUCTION =
  '唐僧一行在松林中遇到一位自称观音座下的年轻僧人，言辞恳切，主动请缨护送西行。' +
  '八戒贪图省事，极力撺掇师父收留；沙僧不动声色，但注意到此人禅杖上刻有不属于佛门的纹路。' +
  '此人身份留白——可以是真心向佛的散修，也可以是某方势力安插的棋子。' +
  '本章以沙僧一个未说出口的疑虑收束。'

function resolveTargetChars(selected: string): number {
  const opt = LENGTH_OPTIONS.find(o => o.value === selected)
  if (opt) return parseInt(opt.value, 10)
  return DEFAULT_TARGET_CHARS
}

function resolveCustomTargetChars(raw: string): number {
  const n = parseInt(raw, 10)
  if (Number.isNaN(n)) return DEFAULT_TARGET_CHARS
  return Math.max(MIN_TARGET_CHARS, Math.min(MAX_TARGET_CHARS, n))
}

function resolveTargetCharsForMode(
  lengthMode: 'preset' | 'custom',
  selectedPreset: string,
  customTargetChars: string,
): number {
  if (lengthMode === 'custom') {
    return resolveCustomTargetChars(customTargetChars)
  }
  return resolveTargetChars(selectedPreset)
}

function clampInt(raw: string, min: number, max: number): number | undefined {
  const n = parseInt(raw, 10)
  if (Number.isNaN(n)) return undefined
  return Math.max(min, Math.min(max, n))
}

export function WritingWorkspace() {
  const { novelId, chapterNum } = useParams<{ novelId: string; chapterNum: string }>()
  const navigate = useNavigate()
  const { user } = useAuth()
  const nId = Number(novelId)
  const cNum = Number(chapterNum)

  const [chapter, setChapter] = useState<Chapter | null>(null)
  const [novelTitle, setNovelTitle] = useState('')
  const [instruction, setInstruction] = useState('')
  const [lengthMode, setLengthMode] = useState<'preset' | 'custom'>('preset')
  const [selectedLength, setSelectedLength] = useState('3000')
  const [customTargetChars, setCustomTargetChars] = useState(String(DEFAULT_TARGET_CHARS))
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const [contextChapters, setContextChapters] = useState(String(DEFAULT_CONTEXT_CHAPTERS))
  const [numVersions, setNumVersions] = useState('1')
  const [temperature, setTemperature] = useState('0.8')
  const [strictMode, setStrictMode] = useState(false)
  const [prefsLoaded, setPrefsLoaded] = useState(false)

  // Load user preferences as defaults (once)
  useEffect(() => {
    if (prefsLoaded || !user?.preferences) return
    const p = user.preferences as Record<string, unknown>
    // Prefer updating state in a callback (not synchronously in the effect body)
    // to avoid cascading renders.
    queueMicrotask(() => {
      if (p.num_versions != null) setNumVersions(String(p.num_versions))
      if (p.temperature != null) setTemperature(String(p.temperature))
      if (p.context_chapters != null) {
        const nextContextChapters = clampInt(String(p.context_chapters), MIN_CONTEXT_CHAPTERS, MAX_CONTEXT_CHAPTERS)
        setContextChapters(String(nextContextChapters ?? DEFAULT_CONTEXT_CHAPTERS))
      }
      if (p.strict_mode != null) setStrictMode(Boolean(p.strict_mode))
      if (p.length_mode === 'custom' || p.length_mode === 'preset') {
        setLengthMode(p.length_mode)
      }
      if (p.target_chars != null) {
        const tc = Number(p.target_chars)
        const match = LENGTH_OPTIONS.find(o => Number(o.value) === tc)
        if (match && p.length_mode !== 'custom') {
          setSelectedLength(match.value)
        } else if (Number.isFinite(tc)) {
          setLengthMode('custom')
          setCustomTargetChars(String(resolveCustomTargetChars(String(tc))))
        }
      }
      setPrefsLoaded(true)
    })
  }, [user?.preferences, prefsLoaded])

  // Save preferences to server when advanced settings change
  const savePrefs = useCallback(() => {
    const prefs: Record<string, unknown> = {}
    const nv = parseInt(numVersions, 10)
    if (!Number.isNaN(nv)) prefs.num_versions = Math.max(1, Math.min(MAX_NUM_VERSIONS, nv))
    const temp = parseFloat(temperature)
    if (!Number.isNaN(temp)) prefs.temperature = Math.max(0, Math.min(2, temp))
    prefs.context_chapters = clampInt(contextChapters, MIN_CONTEXT_CHAPTERS, MAX_CONTEXT_CHAPTERS) ?? DEFAULT_CONTEXT_CHAPTERS
    prefs.length_mode = lengthMode
    prefs.strict_mode = strictMode
    const tc = resolveTargetCharsForMode(lengthMode, selectedLength, customTargetChars)
    prefs.target_chars = tc
    api.updatePreferences(prefs).catch(() => {})
  }, [numVersions, temperature, contextChapters, lengthMode, selectedLength, customTargetChars, strictMode])

  useEffect(() => {
    if (!nId || !cNum) return
    let cancelled = false
    api.getNovel(nId).then(n => {
      if (cancelled) return
      setNovelTitle(n.title)
      if (!demoDefaultApplied.current && n.title === DEMO_NOVEL_TITLE) {
        demoDefaultApplied.current = true
        setInstruction(prev => prev || DEMO_DEFAULT_INSTRUCTION)
      }
    }).catch(() => {})
    api.getChapter(nId, cNum)
      .then(data => { if (!cancelled) setChapter(data) })
      .catch(err => console.error('Failed to load chapter', err))
    return () => { cancelled = true }
  }, [nId, cNum])

  // Pre-fill instruction for demo novel (once, after title loads)
  const demoDefaultApplied = useRef(false)

  const handleGenerate = () => {
    const parsedTemp = parseFloat(temperature)
    const resolvedTargetChars = resolveTargetCharsForMode(lengthMode, selectedLength, customTargetChars)
    const streamParams = {
      prompt: instruction.trim() || undefined,
      length_mode: lengthMode,
      target_chars: resolvedTargetChars,
      context_chapters: clampInt(contextChapters, MIN_CONTEXT_CHAPTERS, MAX_CONTEXT_CHAPTERS) ?? DEFAULT_CONTEXT_CHAPTERS,
      num_versions: clampInt(numVersions, 1, MAX_NUM_VERSIONS) || undefined,
      temperature: !Number.isNaN(parsedTemp) ? Math.max(0, Math.min(2, parsedTemp)) : undefined,
      strict_mode: strictMode,
    }
    savePrefs()
    navigate(`/novel/${novelId}/chapter/${chapterNum}/results`, {
      state: { streamParams, novelId: nId },
    })
  }

  const loadingChapter = !chapter || chapter.novel_id !== nId || chapter.chapter_number !== cNum
  const wordCount = !loadingChapter ? (chapter?.content?.length ?? 0) : 0

  return (
    <PageShell
      className="h-screen"
      navbarProps={{
        position: 'static',
        compact: true,
        hideLinks: true,
        leftContent: (
          <div className="flex items-center gap-4">
            <button
              type="button"
              onClick={() => navigate(`/novel/${novelId}`)}
              className="inline-flex items-center gap-2 bg-transparent border-none p-0 text-sm font-medium text-foreground transition-opacity hover:opacity-80"
            >
              <ArrowLeft size={16} className="text-muted-foreground" />
              <span>{novelTitle || '返回'}</span>
            </button>
          </div>
        ),
      }}
      mainClassName="overflow-hidden"
    >
      <div className="flex flex-1 overflow-hidden">
        {/* Preview Area */}
        <div className="flex-1 min-w-0 flex flex-col gap-6 px-8 py-8 lg:px-12 overflow-hidden">
          <div className="flex items-center justify-between">
            <GlassCard variant="control" className="rounded-xl px-4 py-2">
              <span className="text-sm font-medium text-foreground">
                从第 {cNum} 章继续
              </span>
            </GlassCard>
            <span className="text-sm text-muted-foreground">
              {wordCount} 字
            </span>
          </div>

          <GlassCard className="flex-1 overflow-auto rounded-xl p-6 sm:p-8 nw-scrollbar-thin">
            <PlainTextContent
              isLoading={loadingChapter}
              content={chapter?.content}
              loadingLabel="加载章节内容..."
              emptyLabel="章节暂无内容"
            />
          </GlassCard>
        </div>

        {/* Parameter Panel */}
        <aside className="w-[480px] shrink-0 border-l border-[var(--nw-glass-border)] bg-[var(--nw-glass-bg)] backdrop-blur-2xl p-6 flex flex-col gap-6 overflow-auto nw-scrollbar-thin">
          <h2 className="font-mono text-base font-semibold text-foreground">
            续写设置
          </h2>

          {/* Instruction */}
          <div className="space-y-2">
            <label className="text-sm font-medium text-foreground">
              续写指令（可选）
            </label>
            <Textarea
              value={instruction}
              onChange={e => setInstruction(e.target.value)}
              placeholder="描述你想要的情节走向，或留空让 AI 自由续写"
              className="min-h-[80px] resize-none text-[13px] leading-relaxed bg-[var(--nw-glass-bg)] border-[var(--nw-glass-border)] text-foreground placeholder:text-muted-foreground/70 focus-visible:ring-accent focus-visible:ring-offset-0"
            />
          </div>

          {/* Length */}
          <div className="space-y-2">
            <label className="text-sm font-medium text-foreground">
              续写长度
            </label>
            <div className="grid grid-cols-2 gap-2">
              <button
                type="button"
                onClick={() => setLengthMode('preset')}
                className={cn(
                  'h-9 rounded-[10px] border text-sm font-medium transition-colors',
                  lengthMode === 'preset'
                    ? 'bg-[hsl(var(--accent)/0.12)] border-accent text-accent'
                    : 'bg-[var(--nw-glass-bg)] border-[var(--nw-glass-border)] text-muted-foreground hover:bg-[var(--nw-glass-bg-hover)]',
                )}
              >
                预设档位
              </button>
              <button
                type="button"
                onClick={() => setLengthMode('custom')}
                className={cn(
                  'h-9 rounded-[10px] border text-sm font-medium transition-colors',
                  lengthMode === 'custom'
                    ? 'bg-[hsl(var(--accent)/0.12)] border-accent text-accent'
                    : 'bg-[var(--nw-glass-bg)] border-[var(--nw-glass-border)] text-muted-foreground hover:bg-[var(--nw-glass-bg-hover)]',
                )}
              >
                自定义
              </button>
            </div>
            {lengthMode === 'custom' ? (
              <div className="space-y-1">
                <Input
                  type="number"
                  min={MIN_TARGET_CHARS}
                  max={MAX_TARGET_CHARS}
                  step={100}
                  value={customTargetChars}
                  onChange={e => setCustomTargetChars(e.target.value)}
                  className="h-9 font-mono bg-[var(--nw-glass-bg)] border-[var(--nw-glass-border)] text-foreground placeholder:text-muted-foreground/70 focus-visible:ring-accent focus-visible:ring-offset-0"
                />
                <p className="text-xs text-muted-foreground">
                  {MIN_TARGET_CHARS}–{MAX_TARGET_CHARS} 字
                </p>
              </div>
            ) : null}
            {lengthMode === 'preset' ? (
              <div className="flex gap-2">
                {LENGTH_OPTIONS.map(opt => {
                  const isDisabled = opt.disabled
                  const isSelected = !isDisabled && selectedLength === opt.value
                  return (
                    <button
                      key={opt.value}
                      type="button"
                      onClick={() => !isDisabled && setSelectedLength(opt.value)}
                      disabled={isDisabled}
                      className={cn(
                        'flex-1 h-9 rounded-[10px] border text-sm font-mono transition-colors',
                        isDisabled
                          ? 'bg-muted/50 border-muted text-muted-foreground/40 cursor-not-allowed'
                          : isSelected
                          ? 'bg-[hsl(var(--accent)/0.12)] border-accent text-accent font-semibold'
                          : 'bg-[var(--nw-glass-bg)] border-[var(--nw-glass-border)] text-muted-foreground hover:bg-[var(--nw-glass-bg-hover)]'
                      )}
                    >
                      {opt.label}
                    </button>
                  )
                })}
              </div>
            ) : null}
          </div>

          {/* Advanced Toggle */}
          <button
            type="button"
            onClick={() => setAdvancedOpen(v => !v)}
            className="w-full flex items-center justify-between py-2 text-sm font-medium text-muted-foreground transition-colors hover:text-foreground"
          >
            <span>高级设置</span>
            {advancedOpen ? (
              <ChevronUp size={14} className="text-muted-foreground" />
            ) : (
              <ChevronDown size={14} className="text-muted-foreground" />
            )}
          </button>

          {/* Advanced Panel */}
          <div
            className={cn(
              'grid transition-[grid-template-rows] duration-200',
              advancedOpen ? 'grid-rows-[1fr]' : 'grid-rows-[0fr]'
            )}
          >
            <div className="overflow-hidden">
              <GlassCard className="rounded-xl p-4 flex flex-col gap-4">
                <AdvancedRow label="上下文章节数" desc="1–5" value={contextChapters} onChange={setContextChapters} type="number" min={MIN_CONTEXT_CHAPTERS} max={MAX_CONTEXT_CHAPTERS} step={1} />
                <AdvancedRow label="生成版本数" desc="1–4" value={numVersions} onChange={setNumVersions} type="number" min={1} max={MAX_NUM_VERSIONS} step={1} />
                <AdvancedRow label="创意温度" desc="0.0–2.0" value={temperature} onChange={setTemperature} type="number" min={0} max={2} step={0.1} />
                <div className="flex items-center justify-between gap-4">
                  <div className="flex flex-col gap-0.5 min-w-0">
                    <span className="text-sm font-medium text-foreground">
                      严格一致性
                    </span>
                    <span className="text-xs text-muted-foreground">
                      严格校验设定漂移（实验）
                    </span>
                  </div>
                  <Checkbox
                    checked={strictMode}
                    onCheckedChange={setStrictMode}
                    className="h-5 w-5 rounded border-[var(--nw-glass-border)] data-[state=checked]:bg-accent data-[state=checked]:text-accent-foreground"
                  />
                </div>
              </GlassCard>
            </div>
          </div>

          <div className="flex-1" />

          {/* Generate Button */}
          <NwButton
            data-testid="workspace-generate-button"
            onClick={handleGenerate}
            disabled={!nId}
            variant="accent"
            className="w-full h-12 rounded-xl shadow-[0_4px_24px_hsl(var(--accent)/0.25)] text-[15px] font-semibold disabled:cursor-default"
          >
            <Sparkles size={18} />
            生成续写
          </NwButton>
        </aside>
      </div>
    </PageShell>
  )
}
