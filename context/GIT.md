# Git 관리 가이드

## 원격 저장소

- GitHub: https://github.com/Jgaram/nikke-calc (private)
- 브랜치: `master`

## 주의

커밋 전 반드시 유저에게 커밋 메시지를 제안하고 확인을 받은 후 실행한다. 유저의 승인 없이 커밋하지 않는다.

## 커밋 규칙

### 커밋 메시지 형식

```
[캐릭터명] 추가
[stat명] 구현
fix: [버그 내용]
docs: [문서 업데이트 내용]
```

예시:
```
[신데렐라] 추가
[attack_speed_pct] 구현
fix: 크라운 버프 만료 타이밍 오류
docs: MAINTENANCE.md stat 마스터 테이블 갱신
```

## 커밋 전 주의사항

- `git status`에 `image/` 하위 untracked 파일이 있으면 자동으로 스테이징하지 말고 유저에게 포함 여부를 먼저 확인한다.

## 기본 명령어

```bash
# 현재 상태 확인
git status

# 변경 파일 스테이징
git add .

# 커밋
git commit -m "메시지"

# GitHub에 푸시
git push

# 마지막 커밋 상태로 되돌리기 (저장 안 된 변경사항 전부 취소)
git checkout -- .

# 특정 커밋으로 되돌리기
git log --oneline          # 커밋 해시 확인
git checkout <해시> -- .   # 해당 커밋 상태로 파일 복원
```

## 작업 전 스냅샷

큰 작업 시작 전에 현재 상태를 커밋해두면 언제든 되돌릴 수 있다.

```bash
git add .
git commit -m "작업 전 스냅샷"
```
