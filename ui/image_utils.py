"""캐릭터 이미지 로드 유틸.

st.image()에 로컬 절대경로를 넘기면 Streamlit 보안 정책상 표시되지 않으므로
파일을 읽어 base64 data URI로 변환해 반환한다.
"""

from __future__ import annotations

import base64
import functools
import os

_IMG_DIR = os.path.join(os.path.dirname(__file__), "..", "image")

_MIME = {
    ".webp": "image/webp",
    ".png":  "image/png",
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
}


def _normalize(s: str) -> str:
    """공백·구분자를 제거한 소문자 키 — 파일명과 캐릭터명 비교에 사용."""
    return s.replace(" ", "").replace(":", "").replace("_", "").lower()


@functools.lru_cache(maxsize=1)
def _build_index() -> dict[str, str]:
    """정규화 키 → 실제 파일 절대경로 인덱스 (확장자 포함)."""
    index: dict[str, str] = {}
    for fname in os.listdir(_IMG_DIR):
        stem, ext = os.path.splitext(fname)
        if ext in _MIME:
            index[_normalize(stem)] = os.path.join(_IMG_DIR, fname)
    return index


@functools.lru_cache(maxsize=256)
def get_image_b64(name: str) -> str | None:
    """캐릭터명 → base64 data URI. 이미지 없으면 None."""
    key = _normalize(name.replace(" : ", ""))
    path = _build_index().get(key)
    if path:
        ext = os.path.splitext(path)[1]
        mime = _MIME[ext]
        with open(path, "rb") as f:
            data = base64.b64encode(f.read()).decode()
        return f"data:{mime};base64,{data}"
    return None
