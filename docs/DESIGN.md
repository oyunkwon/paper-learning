# paper-learning 설계

논문 한 편을 **원문을 안 읽어도 될 만큼** 깊게 이해시키는 시스템. 업로드한
논문을 중심으로 grounded 지식 그래프를 만들고, 5가지 뷰로 투영한 뒤, `learning`
프로젝트의 소크라테스식 튜터 루프로 **이해될 때까지 질문하며** 가르친다.

`learning` 프로젝트를 참고하되 그대로 쓰지 않고 재설계한다. 검증된 하위 자산
(LLM 클라이언트, 튜터 루프, PDF 처리, 세션/스토리지 패턴, 진행신호 프로토콜)은
가져오고, 커리큘럼 구조와 플래닝 파이프라인은 새로 짠다. UI는 차용한다.

## 산출물 (사용자가 원한 5가지)

업로드한 논문에 대해:

1. **Landscape** — 이 논문을 이해하기 위한 전체 지형. 문제 영역에 어떤 접근들이
   있고 이 논문이 그 안에서 어디에 앉아 있는가.
2. **선수지식 (Prerequisites)** — "이런 선행연구가 있었다" 수준이 아니라,
   미분방정식·선형대수부터 시작하는 **과목 단위의 넓은 기반지식 지도**. 전공~대학원
   수준까지, 이 논문에 도달하기 위한 학습 경로.
3. **트렌드 & 임팩트** — 이 연구가 속한 분야의 최근 흐름과, 왜 이 연구가 임팩트가
   있는지. 이 논문을 인용한 후속 연구 + 최근 survey 기반.
4. **논문 자체 (Claims & Results)** — 무엇을 주장하고, 왜 이 논문을 썼고, 결과가
   어떻고, 한계가 무엇인지. 원문을 안 읽어도 될 만큼 상세하게.
5. **핵심 인사이트 (Key Insights)** — 이 논문에서 가져갈 핵심 통찰들.

## 핵심 설계 결정

### D1. 단일 grounded 지식 그래프, 5개 뷰

논문 1편당 그래프 1개. 1~5를 독립 리서치 5건으로 보면 소스가 중구난방이 되고
품질 검증이 안 된다. 대신 **논문 중심 grounded knowledge graph 하나**를 만들고
1~5를 그 그래프의 서로 다른 traversal/projection으로 정의한다.

- 노드 = `{개념, 논문, 교재}`
- 엣지 = `{prerequisite-of(개념 의존), cites(인용)}`

투영:

- **(2) 선수지식** = `prerequisite` 엣지의 downward closure를 topological sort.
  → `learning`식 의존성 커리큘럼과 동형. floor(Calculus)~frontier(논문이 전제하는
  개념) 사이를 과목→토픽→개념으로 넓게 편다.
- **(1) Landscape** = references + survey를 클러스터링한 옆(side) 뷰.
- **(3) 트렌드/임팩트** = citing papers + 최근 survey의 앞(forward, 시간) 뷰.
  연도·인용수순.
- **(4)(5)** = 논문 본문 단독 패스의 출력.

### D2. 할루시네이션 차단이 1번 원칙

1·2·3에 등장하는 **모든 외부 논문/교재 인용은 반드시 실제 API에서 retrieve한
메타데이터에서만** 나온다. LLM은 "발굴"이 아니라 retrieve된 abstract를 "요약"만
한다. LLM이 제목·저자·DOI를 생성하면 버린다. 각 소스 노드는 출처 URL/DOI를 들고
다니고, 검증 안 된 인용은 UI에 띄우지 않는다.

### D3. 선수지식 = 과목 단위 broad foundation map (생성 후 grounding)

prereq 트리는 어떤 문서에도 통째로 없으므로 retrieve할 수 없다. 또한 "이 논문은
대칭행렬 스펙트럴 분해의 정확히 이 조각만 쓴다" 같은 외과수술식 슬라이싱은 LLM이
신뢰성 있게 못 한다. 그래서:

- LLM이 **과목(course) 단위의 넓은 의존성 지도**를 생성한다.

  ```
  prereq track
  └─ 과목 그룹 (선형대수 / 미분방정식 / 일반물리 / 확률론 / ... / [대학원 과목])
     └─ 그 과목에서 이 논문 이해에 필요한 토픽들 (의존성 순서)
        └─ 각 토픽 = learning식 chapter (concepts[], 교재 grounding)
  ```

- **floor = Calculus.** 그 아래는 안 판다. 시작 레이어 = 선대/미방/일반물리.
- **ceiling = frontier**: Stage 1에서 분리한 "논문이 전제하는 개념".
- 좁히는 건 LLM이 사전에 하지 않는다. **outline을 항상 풀로 보여주고, 학습자가
  아는 과목/토픽을 체크 해제(`known=true`)해서 빼는** UX로 한다.
- grounding: 과목 단위로 표준 교재 1권 + 토픽 단위로 챕터/강의노트 매핑. 과목
  단위라 retrieve가 안정적이고 할루시네이션이 적다.

  **(개정) prereq는 웹검색 grounding을 하지 않는다.** D2의 할루시네이션 대상은
  landscape/trends의 *구체적 논문 인용*이지, prereq의 정준 과목/토픽이 아니다.
  선형대수·미분방정식·스펙트럴 정리 같은 정준 토픽과 표준 교재명("Gilbert Strang,
  Introduction to Linear Algebra")은 LLM이 신뢰성 있게 안다. 그래서:
  - prereq 과목→토픽→개념 DAG와 교재명은 **LLM 내부지식으로 생성**한다.
  - 단 경계 하나: 특정 URL/판본/페이지를 "검증된 것처럼" 지어내지 않는다.
  - 교재명은 grounded source가 아니라 **group의 advisory 문자열**(`group.textbook`)로
    둔다. `sources[]`(provenance 필수)에는 넣지 않는다 → D2 불변.

### D4. Stage 1의 "전제 개념 vs 도입 개념" 분리가 전체의 축

논문 단독 패스에서 이 분리를 먼저 한다.

- **전제하는(presupposed) 개념** = 선수지식 DAG의 frontier(꼭대기).
- **도입하는(introduced) 개념** = (4)(5)의 대상.

이 분리가 안 되면 1~5가 다 섞인다.

### D5. 4개 트랙, 각 트랙은 learning 커리큘럼 모양

1·2·3·(4+5)를 4개 "트랙"으로 만든다. 각 트랙은 learning의 챕터 리스트와 동형이라
튜터 루프/진행신호/챕터 status 런타임을 **그대로 재사용**한다.

- `prereq` 트랙: `kind="dependency"`. 의존성 순서. 학습자가 노드를 끌 수 있음.
- `landscape` 트랙: `kind="reading"`. 읽고 이해 확인.
- `trends` 트랙: `kind="reading"`.
- `paper` 트랙: `kind="reading"`. 주장/결과/한계/인사이트.

## 파이프라인

```
0. Ingest      PDF 파싱 → title/abstract/refs/본문. arXiv id·DOI 정확 식별.
1. Comprehend  논문 단독 패스: 전제개념 / 도입개념 분리
               + 주장·결과·한계·인사이트(4,5) 추출.
2. Acquire     [전제개념]    → prereq DAG 생성 + 과목 교재 grounding (2)
               [refs+abstract] → landscape survey retrieve + 클러스터 (1)
               [citing papers] → 트렌드·임팩트 retrieve (3)
               (외부 retrieval은 백그라운드 잡, 진행률 스트림)
3. Assemble    노드/엣지를 그래프 1개로 병합. Calculus floor 컷. dedup.
4. Project     그래프 → 4개 트랙 커리큘럼(JSON). 선수지식 outline은 풀로.
5. Tutor       learning 튜터 루프 그대로. 이해될 때까지 질문.
```

## 데이터 모델

```jsonc
session.curriculum = {
  "paper": { "title", "authors", "arxivId", "doi", "abstract", "year" },
  "tracks": [
    {
      "id": "prereq", "kind": "dependency", "floor": "calculus",
      "title": "...", "summary": "...",
      "groups": [                          // 과목 단위 그룹 (prereq 전용)
        { "id": "g_linalg", "title": "선형대수", "textbook": "<source id>",
          "chapterIds": ["ch_..."] }
      ],
      "chapters": [ /* learning Chapter 동형 + 아래 확장 */ ]
    },
    { "id": "landscape", "kind": "reading", "chapters": [...] },
    { "id": "trends",    "kind": "reading", "chapters": [...] },
    { "id": "paper",     "kind": "reading", "chapters": [...] }
  ],
  "graph":   { "nodes": [...], "edges": [...] },   // grounding 보관용
  "sources": [
    { "id", "type": "paper|textbook|survey", "title", "authors",
      "url", "doi", "arxivId", "year", "venue",
      "retrievedFrom": "semantic_scholar|openalex|arxiv|web" }
  ]
}
```

**Chapter** (learning과 동형):
```jsonc
{
  "id": "ch1", "title": "...", "summary": "...",
  "concepts": ["...", ...],
  "conceptsDone": [false, ...],            // 런타임
  "status": "active|locked|done",          // 런타임
  "pageStart": null, "pageEnd": null,      // paper 트랙만 의미 있음
  // --- paper-learning 확장 ---
  "sourceIds": ["..."],                    // 이 챕터의 grounding 소스
  "known": false                           // prereq 트랙: 학습자가 끄면 true
}
```

## 외부 retrieval 레이어 (신규)

| 소스 | 용도 |
|---|---|
| Semantic Scholar API | 인용그래프(references/citations), influential citations, abstract |
| OpenAlex API | 인용그래프 보강, venue/year, concept 태그 |
| arXiv API | 논문 메타/검색, survey 탐색 |
| 웹검색 (Tavily/Brave 등) | prereq 과목 교재·강의노트 grounding |

- 논문을 arXiv id/DOI로 정확 식별하는 게 모든 API 호출의 전제.
- 응답은 캐시 (재실행/레이트리밋 대비).
- backward(references)+abstract → landscape. forward(citing)+influential →
  트렌드/임팩트. Semantic Scholar influential-citation 플래그로 load-bearing
  선행연구만 추림.

## learning에서 재사용 / 신규 정리

**그대로 (검증됨):**
- `llm.py` — 멀티모달+스트리밍 클라이언트
- `tutor.py` — 튜터 루프, sentinel 파싱, 루프/자문자답 가드 (트랙 무관)
- `material.py` — PDF 렌더/텍스트/배칭
- 세션/DB/스토리지 패턴 (Postgres+JSONB+R2캐시)
- 백그라운드 잡 + status/progress 폴링 패턴 (Stage 2 비동기 요구와 일치)
- sentinel 진행신호 프로토콜 (`<<TEACHING>>`/`<<CONCEPT_DONE>>`/`<<MASTERED>>`)

**재설계:**
- 커리큘럼: 단일 챕터 리스트 → 멀티트랙 + 그래프 + 소스
- 플래너: 문서 map-reduce → 5뷰 파이프라인 (Comprehend→Acquire→Assemble→Project)

**신규:**
- retrieval 레이어 (Semantic Scholar/OpenAlex/arXiv/web + 캐시)
- 논문 식별 (arXiv id/DOI 추출)
- Stage 1 전제/도입 개념 분리 프롬프트
- 멀티트랙 커리큘럼 스키마 + outline 체크해제 UI

**UI 차용:**
- 사이드바 / 채팅 페인 / 커리큘럼 바 / 업로드존 / shadcn 컴포넌트
- 멀티트랙은 커리큘럼 바를 트랙 탭/섹션으로 확장

## Retrieval 검증 결과 (프로토타입 완료)

실제 API로 "Attention Is All You Need"(arXiv 1706.03762)를 넣어 grounding이
되는지 검증했다. 결론: **된다.** 역할 분담과 함정이 확정됨.

**역할 분담 (확정):**

| API | 역할 | 근거 |
|---|---|---|
| OpenAlex | **landscape + trends backbone**. references(referenced_works) + citing papers, 둘 다 cited_by_count desc 정렬 + topics 분야 계층 | 무료·무키·견고. 한 API로 1·3뷰 모두. |
| Semantic Scholar | references의 `isInfluential` **보강(enrichment)만** | 키 없이는 rate-limit이 빡셈(429). 죽어도 시스템 안 망가지게 보조로만. |
| arXiv | survey/논문 검색 | **https 필수** (http는 301) |

**(개정) OpenAlex가 landscape backbone이다.** 초기엔 S2 references(`isInfluential`)를
landscape 주력으로 잡았으나, 구현 중 **S2가 무키 상태에서 429로 쉽게 죽는 것**을
확인. 그래서 landscape도 OpenAlex `referenced_works`(→ batch 메타 조회, 인용수
정렬)로 만들고, S2의 influential 플래그는 **있으면 doi/arxiv/title 매칭으로 덧입히고
없으면 스킵**하는 enrichment로 강등했다. 결과: 무료 API 하나(OpenAlex)만으로 1·3뷰가
견고하게 나오고, S2 rate-limit이 더 이상 치명적이지 않다. (live 테스트는 S2/OpenAlex
rate-limit 시 fail이 아니라 skip.)

**식별 파이프라인 (함정 #1 반영):**
```
arXiv id → S2 paper/arXiv:{id} (즉시 식별, externalIds로 DOI 확보)
         → OpenAlex는 DOI 또는 title search로 canonical id 해석
```
OpenAlex id를 S2의 MAG id로 추측하면 안 된다. `/works/{MAG_id}`는 404가 난다
(예: MAG `W2963403868` → 실제 canonical `W2626778328`). `filter=cites:{MAG_id}`는
레거시 id를 받아주지만 직접 조회는 안 되므로, **반드시 DOI/title로 resolve**한 뒤
그 canonical id로 topics/citing을 친다.

**DOI가 없는 논문도 흔하다 (구현 중 확인):** "Attention Is All You Need"의 S2
externalIds엔 MAG/DBLP/ArXiv/CorpusId만 있고 **DOI가 없다**. 그래도 OpenAlex
canonical id는 **title search fallback**으로 정상 resolve된다. → title fallback은
선택이 아니라 **필수 경로**. (모듈/라이브 테스트로 박음.)

**함정 (설계 반영 필수):**
1. OpenAlex canonical id는 DOI/title search로만 안전하게 얻는다 (위 참조).
2. **S2 citations 엔드포인트는 정렬을 안 해준다** (기본 최신순, 17만 개). 트렌드용
   고임팩트 citing 추출은 반드시 OpenAlex 정렬로. S2 citations는 트렌드 backbone
   으로 부적합.
3. **메타데이터 잡음**: title 누락, 인용수 오염, publication_year 이상치가 섞인다.
   → title 없으면 버리고, `publication_year` 필터 + sanity 체크 필수.

**운영 메모:**
- OpenAlex는 `mailto=` 파라미터로 polite pool 사용 권장 (안정적 레이트).
- 모든 응답은 캐시 (재실행/레이트리밋 대비).

## 미해결 / 다음 결정

- Stage 2 retrieval 잡의 정확한 진행률 분해 (배치 수 산정)
- prereq 과목 교재 grounding의 웹검색 vs 큐레이션된 정준 교재 매핑 우선순위
- 트랙별 챕터 수 상한 (커리큘럼 폭주 방지)
- 멀티트랙에서 튜터 컨텍스트: 트랙 내 챕터만 vs 그래프 일부 동반
