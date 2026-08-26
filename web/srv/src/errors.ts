// 오류 → 상태 매핑 (계약 §0): InputError→400 · BusyError→429 · OpsError→502 · 그 밖→500 고정 문장.
export class OpsError extends Error {} // 파이썬 RuntimeError에 해당 — 설명 가능한 실패(코어 불가·저장 실패 등)
