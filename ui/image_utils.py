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


def _char_to_filename(name: str) -> str:
    return name.replace(" : ", " _ ")


@functools.lru_cache(maxsize=256)
def get_image_b64(name: str) -> str | None:
    """캐릭터명 → base64 data URI. 이미지 없으면 None."""
    stem = _char_to_filename(name)
    for ext, mime in _MIME.items():
        path = os.path.join(_IMG_DIR, stem + ext)
        if os.path.exists(path):
            with open(path, "rb") as f:
                data = base64.b64encode(f.read()).decode()
            return f"data:{mime};base64,{data}"
    return None
