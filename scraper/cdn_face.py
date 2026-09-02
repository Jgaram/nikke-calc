"""얼굴 카드 수집 — 스쿼드 캡처 판독용 대조군.

    python scraper/cdn_face.py            # 증분 — 새 니케·새 코스튬만 (기본)
    python scraper/cdn_face.py --full     # 전체 훑기 (처음 한 번, 또는 표가 어긋났을 때)
    python scraper/cdn_face.py --force    # 이미 받은 카드도 다시 (--full 을 함의)

**기본이 증분이다.** 전체 훑기는 CDN에 6,700건을 던지는데 그중 5,000건 넘게가
«없는 조합»을 묻는 헛걸음이다. 증분은 아는 것을 안 묻고 새 것만 확인한다.

경로: `/character/si/si_c{리소스id:03d}_{코스튬:02d}_s.webp` (68×68 정사각).
블라링크 «니케 도감»의 작은 카드가 쓰는 바로 그 그림이고, 인게임 스쿼드 목록
캡처에 뜨는 얼굴과 같다. 초상화(`/character/mi/mi_c###_00_s.webp`)는 그림이
달라서 대조군으로 못 쓴다 — 자름틀을 20가지로 훑어도 1등 점수가 0.5대에
2등과의 차이가 0.02밖에 안 나온다(실측).

**코스튬(스킨)마다 얼굴이 다르다.** 같은 니케라도 00·01·02가 전부 다른 그림이라
번호를 끝까지 훑어 모아 두고, 판독할 때 «어느 코스튬이든 맞으면 그 니케»로 친다.

산출물:
  image/face/si_c###_##.webp   얼굴 카드
  data/face_index.json         파일 → 리소스id·코스튬·이름·속성
"""
from __future__ import annotations

import argparse
import collections
import concurrent.futures as cf
import json
import os
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT_DIR = os.path.join(ROOT, "image", "face")
MAP_PATH = os.path.join(ROOT, "data", "face_index.json")
sys.path.insert(0, HERE)
import cdn_path  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

PATH = "/character/si/si_c{rid:03d}_{cos:02d}_s.webp"
ROLE = "/roledata/{rid}-v2-ko.json"
# roledata의 element_details[0].element -> 게임 안 표기. 판독이 쓰는 이름과 같아야 한다
# (squad_ocr.ELEM_HUE의 키다). 다섯 개가 전부다.
ELEM_KO = {"Fire": "작열", "Water": "수냉", "Wind": "풍압",
           "Electronic": "전격", "Iron": "철갑"}
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/151.0.0.0 Safari/537.36"}
# 코스튬 번호는 촘촘하지 않다 — 중간이 비어도 뒤에 더 있을 수 있다.
MAX_COSTUME = 24
# 연속 이만큼 없으면 그 니케는 끝으로 본다. 실측 구멍은 «rid 72 = 0,1,2,4» 한 칸짜리
# 하나뿐이고 최대 코스튬이 4다. 3이면 두 칸 구멍까지 견딘다 — 1이나 2로 줄이지 마라.
MISS_STREAK = 3
# 리소스 번호는 «우리가 아는 199명»에 갇히지 않는다. CDN을 직접 훑어야 빠진 니케가
# 안 생긴다(실측: 카드가 있는 리소스가 232개 — 우리가 모르던 게 33개였다).
MAX_RID = 900


def get(path: str) -> bytes | None:
    url = cdn_path.CDN_BASE + "/" + cdn_path.obfuscate(path)
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=15) as r:
            b = r.read()
    except Exception:                          # noqa: BLE001  없는 코스튬이 훨씬 많다
        return None
    # 200이어도 이미지가 아닐 수 있다 — SPA 폴백을 파일로 저장하면 나중에 깨진
    # 아이콘으로만 드러나 원인을 찾기 어렵다.
    return b if b.startswith(b"RIFF") and b[8:12] == b"WEBP" else None


def get_json(path: str):
    b = get_raw(path)
    try:
        return json.loads(b) if b else None
    except Exception:                          # noqa: BLE001
        return None


def get_raw(path: str) -> bytes | None:
    url = cdn_path.CDN_BASE + "/" + cdn_path.obfuscate(path)
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=15) as r:
            return r.read()
    except Exception:                          # noqa: BLE001
        return None


def main() -> None:
    ap = argparse.ArgumentParser(description="니케 얼굴 카드 수집")
    ap.add_argument("--full", action="store_true",
                    help="리소스 1~%d를 전부 훑는다 (처음 한 번, 또는 표가 어긋났을 때)"
                         % (MAX_RID - 1))
    ap.add_argument("--force", action="store_true",
                    help="이미 받은 카드도 다시 받는다 (--full 을 함의)")
    args = ap.parse_args()
    os.makedirs(OUT_DIR, exist_ok=True)

    old = {}
    if os.path.exists(MAP_PATH):
        old = json.load(open(MAP_PATH, encoding="utf-8")).get("faces") or {}
    full = args.full or args.force or not old
    if full and not (args.full or args.force):
        print("[!] 조회표가 없다 — 전체 훑기로 돈다")

    have = {f for f in os.listdir(OUT_DIR) if f.endswith(".webp")}
    known = collections.defaultdict(set)          # rid -> 이미 아는 코스튬 번호
    for v in old.values():
        known[v["rid"]].add(v["cos"])

    # 1) 어디부터 볼 것인가.
    #    전체:  1~899를 코스튬 00으로 훑어 카드가 있는 리소스를 찾는다.
    #    증분:  «모르는 번호»만 훑는다. 아는 니케는 이미 있는 걸 아니까 안 묻고,
    #           대신 마지막 코스튬 **다음 번호부터** 새 스킨이 붙었는지만 본다.
    if full:
        with cf.ThreadPoolExecutor(24) as ex:
            rids = [r for r in ex.map(
                lambda r: r if get(PATH.format(rid=r, cos=0)) else None, range(1, MAX_RID))
                if r]
        start = {r: 0 for r in rids}
        print(f"[+] 전체 훑기 — 카드가 있는 리소스 {len(rids)}개")
    else:
        unknown = [r for r in range(1, MAX_RID) if r not in known]
        with cf.ThreadPoolExecutor(24) as ex:
            found = [r for r in ex.map(
                lambda r: r if get(PATH.format(rid=r, cos=0)) else None, unknown) if r]
        start = {r: max(cs) + 1 for r, cs in known.items()}
        start.update({r: 0 for r in found})
        rids = sorted(start)
        print(f"[+] 증분 — 새 리소스 {len(found)}개 · 아는 리소스 {len(known)}개")

    # 2) 이름과 속성 — roledata 한 번에 둘 다 있다. 이름이 없으면 미출시·NPC라
    #    판독 후보에서 뺀다(가질 수 없는 니케가 오답으로 튀어나오면 손해다).
    #    아는 니케는 다시 묻지 않는다 — 단, 그때 이름이 없었다면 이제 나왔을 수 있다.
    res_name = {str(v["rid"]): v["name"] for v in old.values() if v.get("name")}
    res_elem = {str(v["rid"]): v["elem"] for v in old.values() if v.get("elem")}
    # name_code는 «게임이 그 니케를 부르는 번호»다. 남의 사이트 편성 코드가 이 번호로
    # 오므로(미미르 등) 이름을 맞추려면 표가 필요하다.
    res_code = {str(v["rid"]): v["code"] for v in old.values() if v.get("code")}
    need = [r for r in rids if full or str(r) not in res_name
            or str(r) not in res_elem or str(r) not in res_code]
    if need:
        with cf.ThreadPoolExecutor(12) as ex:
            for r, d in zip(need, ex.map(lambda r: get_json(ROLE.format(rid=r)), need)):
                if not isinstance(d, dict):
                    continue
                if d.get("name_localkey"):
                    res_name[str(r)] = d["name_localkey"]
                ko = ELEM_KO.get((d.get("element_details") or [{}])[0].get("element"))
                if ko:
                    res_elem[str(r)] = ko
                if d.get("name_code"):
                    res_code[str(r)] = d["name_code"]
    named = sum(1 for r in rids if str(r) in res_name)
    print(f"[+] 이름 물어본 리소스 {len(need)}개 · 이름 있음 {named}개 "
          f"· 이름 없음 {len(rids) - named}개")

    # 3) 코스튬 — 연속 MISS_STREAK번 없으면 그 니케는 끝이다. 24번까지 매번 묻는
    #    것이 헛걸음의 대부분이었다(실측 최대 코스튬은 4다).
    def walk(rid):
        out, miss = [], 0
        for cos in range(start[rid], MAX_COSTUME):
            fname = f"si_c{rid:03d}_{cos:02d}.webp"
            if fname in have and not args.force:
                out.append((fname, rid, cos, 0))
                miss = 0
                continue
            data = get(PATH.format(rid=rid, cos=cos))
            if not data:
                miss += 1
                if miss >= MISS_STREAK:
                    break
                continue
            with open(os.path.join(OUT_DIR, fname), "wb") as f:
                f.write(data)
            out.append((fname, rid, cos, len(data)))
            miss = 0
        return out

    # 증분은 **기존 표 위에 얹는다.** 새로 만들면 이번에 안 본 카드가 다 사라진다.
    index = {} if full else dict(old)
    got = skip = 0
    with cf.ThreadPoolExecutor(24) as ex:
        for out in ex.map(walk, rids):
            for fname, rid, cos, n in out:
                index[fname] = {"rid": rid, "cos": cos,
                                "name": res_name.get(str(rid)),
                                "elem": res_elem.get(str(rid)),
                                "code": res_code.get(str(rid))}
                if n:
                    got += 1
                else:
                    skip += 1
    # 아는 니케의 이름·속성이 이번에 채워졌을 수 있으니 옛 줄도 갱신해 둔다
    for v in index.values():
        v["name"] = res_name.get(str(v["rid"]), v.get("name"))
        v["elem"] = res_elem.get(str(v["rid"]), v.get("elem"))
        v["code"] = res_code.get(str(v["rid"]), v.get("code"))

    with open(MAP_PATH, "w", encoding="utf-8") as f:
        json.dump({"_comment": "얼굴 카드 → 리소스id·코스튬·이름·속성·게임 코드. name이 null이면 "
                               "이름을 못 찾은 것(미출시·NPC)이라 판독 후보에서 뺀다. "
                               "python scraper/cdn_face.py 로 갱신.", "faces": index},
                  f, ensure_ascii=False, indent=1)
    chars = len({v["rid"] for v in index.values()})
    size = sum(os.path.getsize(os.path.join(OUT_DIR, f)) for f in os.listdir(OUT_DIR))
    print(f"[+] 새로 {got}장 · 건너뜀 {skip}장 · 총 {len(index)}장 / {chars}명 "
          f"({size / 1048576:.1f} MB) → {OUT_DIR}")
    print(f"[+] 조회표: {MAP_PATH}")
    if got:
        print("[!] 새 카드가 있다 — python scraper/face_sig.py 로 서명표를 다시 구워라")


if __name__ == "__main__":
    main()
