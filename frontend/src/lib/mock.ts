// In-memory mock backend for frontend development without W1/W3 (see api.ts
// shouldMock). The seed curriculum mirrors the real plan_paper output for
// "Attention Is All You Need" so the multi-track UI can be exercised end to end:
// 4 tracks, prereq course groups + textbooks, grounded sources on landscape/
// trends chapters, and a synthesized tutor token stream.

import type {
  ChatStreamArgs,
  ChatStreamHandlers,
} from "@/lib/chatStream"
import type {
  Chapter,
  CreateSessionResponse,
  Curriculum,
  SessionDetail,
  SessionSummary,
  Threads,
  TrackId,
} from "@/types"

// ----- seed data -----------------------------------------------------------

function ch(
  id: string,
  title: string,
  concepts: string[],
  extra: Partial<Chapter> = {},
): Chapter {
  return {
    id,
    title,
    summary: extra.summary ?? "",
    concepts,
    conceptsDone: concepts.map(() => false),
    status: "locked",
    pageStart: extra.pageStart ?? null,
    pageEnd: extra.pageEnd ?? null,
    sourceIds: extra.sourceIds ?? [],
    known: extra.known,
  }
}

function seedCurriculum(): Curriculum {
  const tracks: Curriculum["tracks"] = [
    {
      id: "paper",
      kind: "reading",
      title: "논문 자체",
      summary: "Transformer 논문의 주장·방법·결과·한계·인사이트.",
      chapters: [
        ch("paper1", "문제와 동기: 순차 계산의 벽", [
          "RNN의 순차 의존성 문제",
          "장거리 의존성 학습의 어려움",
          "병렬화 제약",
        ]),
        ch("paper2", "핵심 아이디어: 어텐션 메커니즘", [
          "Scaled Dot-Product Attention",
          "Query/Key/Value",
          "Multi-Head Attention",
          "셀프 어텐션",
        ]),
        ch("paper3", "Transformer 아키텍처 조립", [
          "인코더-디코더 구조",
          "위치 인코딩",
          "잔차 연결과 층 정규화",
        ]),
        ch("paper4", "결과: 성능과 효율의 동시 달성", [
          "WMT 번역 SOTA",
          "학습 비용 절감",
          "일반화(구문 분석)",
        ]),
        ch("paper5", "한계와 인사이트", [
          "이차 복잡도",
          "어텐션의 해석 가능성",
          "후속 연구로의 확장성",
        ]),
      ],
    },
    {
      id: "prereq",
      kind: "dependency",
      title: "선수지식",
      summary: "미적분 이후, 이 논문에 도달하기 위한 과목 단위 기반지식.",
      groups: [
        { id: "g1", title: "선형대수", textbook: "Gilbert Strang, Introduction to Linear Algebra", chapterIds: ["p_la1", "p_la2"] },
        { id: "g2", title: "확률론과 정보이론", textbook: "Sheldon Ross, A First Course in Probability", chapterIds: ["p_pr1", "p_pr2"] },
        { id: "g3", title: "딥러닝 기초", textbook: "Goodfellow, Bengio, Courville, Deep Learning", chapterIds: ["p_dl1", "p_dl2"] },
        { id: "g4", title: "시퀀스 모델링과 NLP", textbook: "Jurafsky & Martin, Speech and Language Processing", chapterIds: ["p_sq1", "p_sq2"] },
      ],
      chapters: [
        ch("p_la1", "벡터와 행렬 연산", ["벡터공간", "행렬곱", "내적과 노름"]),
        ch("p_la2", "고유값과 행렬 분해", ["고유벡터", "직교성", "softmax 정규화"]),
        ch("p_pr1", "확률 기초", ["조건부 확률", "기댓값", "분포"]),
        ch("p_pr2", "정보이론", ["엔트로피", "교차 엔트로피", "KL 발산"]),
        ch("p_dl1", "신경망과 역전파", ["퍼셉트론", "연쇄법칙", "경사하강법"]),
        ch("p_dl2", "심층망 학습 안정화", ["층 정규화", "드롭아웃", "잔차 연결"]),
        ch("p_sq1", "순환 신경망과 시퀀스", ["RNN", "LSTM/GRU", "임베딩"]),
        ch("p_sq2", "인코더-디코더와 어텐션", ["seq2seq", "정렬", "어텐션 가중치"]),
      ],
    },
    {
      id: "landscape",
      kind: "reading",
      title: "전체 지형 (Landscape)",
      summary: "이 논문이 인용한 선행연구로 본 문제 영역의 지형.",
      chapters: [
        ch("land1", "순환 신경망 기반 시퀀스 모델링의 토대", ["RNN 인코더-디코더", "장기 의존성"], { sourceIds: ["s_seq2seq", "s_gru"] }),
        ch("land2", "어텐션 메커니즘: 중심축", ["가산 어텐션", "정렬 학습"], { sourceIds: ["s_bahdanau"] }),
        ch("land3", "순환을 대체하는 합성곱 계열", ["ConvS2S", "병렬 시퀀스 처리"], { sourceIds: ["s_convs2s"] }),
      ],
    },
    {
      id: "trends",
      kind: "reading",
      title: "트렌드 & 임팩트",
      summary: "이 논문을 인용한 후속 연구로 본 최근 흐름과 임팩트.",
      chapters: [
        ch("trend1", "사전학습 언어모델의 폭발", ["BERT", "사전학습-미세조정"], { sourceIds: ["s_bert"] }),
        ch("trend2", "비전으로 건너간 트랜스포머", ["ViT", "패치 임베딩"], { sourceIds: ["s_vit"] }),
        ch("trend3", "과학 난제로의 파급", ["AlphaFold", "구조 예측"], { sourceIds: ["s_alphafold"] }),
      ],
    },
  ]

  // First chapter of each track starts active.
  for (const t of tracks) if (t.chapters[0]) t.chapters[0].status = "active"

  return {
    paper: {
      title: "Attention Is All You Need",
      authors: ["Ashish Vaswani", "Noam Shazeer", "et al."],
      arxivId: "1706.03762",
      doi: null,
      year: 2017,
      abstract:
        "The dominant sequence transduction models are based on complex recurrent or convolutional neural networks… We propose a new simple network architecture, the Transformer, based solely on attention mechanisms.",
      venue: "NeurIPS",
    },
    tracks,
    sources: [
      src("s_seq2seq", "Sequence to Sequence Learning with Neural Networks", 2014),
      src("s_gru", "Learning Phrase Representations using RNN Encoder-Decoder", 2014),
      src("s_bahdanau", "Neural Machine Translation by Jointly Learning to Align and Translate", 2014),
      src("s_convs2s", "Convolutional Sequence to Sequence Learning", 2017),
      src("s_bert", "BERT: Pre-training of Deep Bidirectional Transformers", 2019),
      src("s_vit", "An Image is Worth 16x16 Words: Transformers for Image Recognition", 2020),
      src("s_alphafold", "Highly accurate protein structure prediction with AlphaFold", 2021),
    ],
  }
}

function src(id: string, title: string, year: number) {
  return {
    id,
    type: "paper" as const,
    title,
    authors: [],
    url: `https://example.org/${id}`,
    doi: null,
    arxivId: null,
    year,
    venue: null,
    retrievedFrom: "openalex",
  }
}

// ----- in-memory store ------------------------------------------------------

interface MockSession {
  summary: SessionSummary
  curriculum: Curriculum
  threads: Threads
}

const store = new Map<string, MockSession>()

function ensureSeed(): MockSession {
  const id = "mock-attention"
  let s = store.get(id)
  if (!s) {
    s = {
      summary: {
        id,
        title: "Attention Is All You Need",
        filename: "attention.pdf",
        kind: "pdf",
        createdAt: Date.now() / 1000,
        status: "ready",
      },
      curriculum: seedCurriculum(),
      threads: {},
    }
    store.set(id, s)
  }
  return s
}

function findChapter(c: Curriculum, trackId: TrackId, chapterId: string) {
  const track = c.tracks.find((t) => t.id === trackId)
  return track?.chapters.find((ch) => ch.id === chapterId) ?? null
}

// ----- mock API -------------------------------------------------------------

export async function listSessions(): Promise<SessionSummary[]> {
  ensureSeed()
  return [...store.values()].map((s) => s.summary)
}

export async function getSession(id: string): Promise<SessionDetail> {
  const s = store.get(id) ?? ensureSeed()
  return { ...s.summary, curriculum: s.curriculum, threads: s.threads }
}

export async function createSession(file: File): Promise<CreateSessionResponse> {
  // Pretend we planned the uploaded file; reuse the seed curriculum.
  const seed = ensureSeed()
  return { ...seed.summary, title: file.name, curriculum: seed.curriculum }
}

export async function deleteSession(id: string): Promise<void> {
  store.delete(id)
}

export async function renameSession(
  id: string,
  title: string,
): Promise<SessionSummary> {
  const s = store.get(id) ?? ensureSeed()
  s.summary = { ...s.summary, title }
  return s.summary
}

export async function setKnown(
  id: string,
  trackId: TrackId,
  chapterId: string,
  known: boolean,
): Promise<Curriculum> {
  const s = store.get(id) ?? ensureSeed()
  const c = findChapter(s.curriculum, trackId, chapterId)
  if (c) {
    c.known = known
    if (known) {
      c.status = "done"
      c.conceptsDone = c.concepts.map(() => true)
    } else {
      c.status = "active"
      c.conceptsDone = c.concepts.map(() => false)
    }
  }
  return s.curriculum
}

export async function activateChapter(
  id: string,
  trackId: TrackId,
  chapterId: string,
): Promise<Curriculum> {
  const s = store.get(id) ?? ensureSeed()
  const c = findChapter(s.curriculum, trackId, chapterId)
  if (c && c.status !== "done") c.status = "active"
  return s.curriculum
}

export async function passConcept(
  id: string,
  trackId: TrackId,
  chapterId: string,
): Promise<{ curriculum: Curriculum; passedIndex: number; chapterDone: boolean }> {
  const s = store.get(id) ?? ensureSeed()
  const c = findChapter(s.curriculum, trackId, chapterId)
  let passedIndex = -1
  let chapterDone = false
  if (c) {
    const idx = c.conceptsDone.findIndex((d) => !d)
    if (idx !== -1) {
      c.conceptsDone[idx] = true
      passedIndex = idx
    }
    chapterDone = c.conceptsDone.every(Boolean)
    if (chapterDone) c.status = "done"
  }
  return { curriculum: s.curriculum, passedIndex, chapterDone }
}

// Synthesize a tutor turn: stream a short canned reply token by token, then
// emit a concept_done (or mastered on the last concept) so progress advances.
export async function streamChat(
  id: string,
  args: ChatStreamArgs,
  handlers: ChatStreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const s = store.get(id) ?? ensureSeed()
  const c = findChapter(s.curriculum, args.trackId, args.chapterId)
  if (!c) {
    handlers.onError?.("챕터를 찾을 수 없습니다.")
    return
  }
  const idx = Math.max(0, c.conceptsDone.findIndex((d) => !d))
  const concept = c.concepts[idx] ?? c.concepts[0] ?? c.title
  const reply = args.kickoff
    ? `**${c.title}** 챕터를 시작할게요. 먼저 *${concept}* 부터 봅시다.\n\n이 개념을 어떻게 이해하고 있는지 한 문장으로 설명해줄래요?`
    : `좋아요. *${concept}* 에 대한 답을 살펴봤어요. 한 걸음 더: 이걸 다른 상황에 적용하면 어떻게 될까요?`

  const words = reply.split(/(\s+)/)
  for (const w of words) {
    if (signal?.aborted) return
    handlers.onToken?.(w)
    await sleep(18)
  }

  // Advance progress on a real (non-kickoff) answer.
  if (!args.kickoff) {
    c.conceptsDone[idx] = true
    handlers.onConceptDone?.(c.id, idx)
    if (c.conceptsDone.every(Boolean)) {
      c.status = "done"
      handlers.onMastered?.(c.id)
    }
  }
  handlers.onDone?.(reply)
}

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms))
}
