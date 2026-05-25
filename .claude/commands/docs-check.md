# check-docs

코드↔context/*.md 불일치 목록 보고.

## 범위 매핑

$ARGUMENTS:

| 입력 | 대상 |
|------|------|
| `ui`, `화면`, `앱` | `app.py`, `ui/` ↔ `context/UI.md` |
| `calc`, `calculator`, `계산기`, `damage`, `buff` | `calculator/` ↔ `context/CALCULATOR.md`, `context/IMPL-STATUS.md` |
| `scraper`, `scrape`, `스크래퍼`, `파싱`, `parsing` | `scraper/` ↔ `context/SCRAPER.md`, `context/PARSING.md` |
| `data`, `데이터` | `data/` ↔ `context/DATA_VERIFY.md` |
| `all`, `전체`, `` (없음) | 전체 |

표에 없으면 유사 항목 추론.

## 절차

1. 범위 코드 파일 읽는다.
2. 범위 context/*.md 읽는다.
3. 교차 검증:
   - 함수·클래스·메서드 이름 (문서 언급 = 코드 존재?)
   - 파라미터·반환값·필드 이름
   - 파일 경로 (문서 경로 = 실제 존재?)
   - 동작 설명 일치 여부
   - 삭제·이름 변경 항목이 문서에 잔존하는가

## 출력

불일치마다:
```
[파일:라인] vs [문서 파일 섹션]
- 문서: "..."
- 코드: "..."
- 판정: 구식 문서 / 미구현 / 이름 불일치 / 기타
```

없으면 "불일치 없음".
