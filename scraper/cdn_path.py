"""blablalink CDN 경로 난독화 재현.

사이트 프론트엔드(`index-*.js`)의 `obfuscatedPath()`와 동일한 규칙:

- 디렉토리 세그먼트 → djb2 해시(고정 소수) 기반 `xx-99` 토큰
- 파일명 → md5(평문 전체 경로) + 원래 확장자

평문 경로만 알면 CDN URL이 결정되므로 브라우저가 필요 없다.
스크래핑 대상 경로는 `cdn_fetch.py` 참조.
"""

import ctypes
import hashlib

CDN_BASE = "https://sg-tools-cdn.blablalink.com"

# 프론트엔드의 LARGE_PRIMES. 디렉토리 깊이별로 하나씩 쓴다.
LARGE_PRIMES = [224737, 1000639, 2654435761, 2654435769, 1000621, 4294967291]


def _djb2(text: str, seed: int) -> int:
    """JS 비트연산(ToInt32) 시맨틱을 그대로 따라간다."""
    value = seed
    for ch in text:
        value = ctypes.c_int32((value * 33 + ord(ch)) & 0xFFFFFFFF).value
    return value


def _dir_token(path: str, prime: int) -> str:
    r = (_djb2(path, prime) % prime + prime) % prime
    letters = chr(97 + (r // 26) % 26) + chr(97 + r % 26)
    return f"{letters}-{str(r % 99).rjust(2, '0')}"


def obfuscate(path: str) -> str:
    """평문 경로 → 난독화된 CDN 상대 경로."""
    plain = path.lstrip("/")
    segments = [s for s in plain.split("/") if s]
    out = []
    for i, seg in enumerate(segments):
        if i == len(segments) - 1:
            ext = ".".join(seg.split(".")[1:])
            out.append(f"{hashlib.md5(plain.encode()).hexdigest()}.{ext}")
        else:
            out.append(_dir_token(plain, LARGE_PRIMES[i]))
    return "/".join(out)


def url(path: str) -> str:
    """평문 경로 → 최종 CDN URL."""
    return f"{CDN_BASE}/{obfuscate(path)}"
