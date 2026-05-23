# 작업 워크플로우

## UI

**주소:** http://localhost:8501

실행: `run.bat` 더블클릭 또는 터미널에서 `streamlit run app.py`

구현 확인은 여기서 한다.

## 신규 캐릭터 추가

| 단계 | 슬래시 커맨드 | 내용 |
|------|--------------|------|
| 1 | `/char-scrape` | 스크래퍼 실행, `parsed_nikke.json` 갱신, SR/RL `weapon_delays.json` 처리 |
| 2 | `/char-parse` | 스킬 파싱, 새 stat 확인 |
| 3 | `/char-impl` | 계산기 구현, test.py 검증 |

2단계부터 시작하는 경우가 많다. 각 단계 완료 후 다음 단계 진행 여부를 Claude에게 말한다.

## 기타 커맨드

| 슬래시 커맨드 | 내용 |
|--------------|------|
| `/bug-fix` | calculator 버그 수정 |
| `/docs-check` | 코드↔문서 불일치 확인 |
| `/commit` | 변경 사항 그룹핑 후 커밋 |
