"""OG 썸네일 — 1200×630, 어두운 판 + 수영복 아니스(c015) 전신 일러.

    python web/og_image.py            # web/src/og.png 갱신
"""
from PIL import Image, ImageDraw, ImageFont
import pathlib, sys

ROOT = pathlib.Path(r"C:\claude\nikke-calc")
OUT = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "web" / "src" / "og.png"
W, H = 1200, 630
art = Image.open(ROOT / "image" / "full" / "c015.webp").convert("RGBA")
print("art size", art.size)

bg = Image.new("RGBA", (W, H), (21, 22, 26, 255))
# 오른쪽에 비스듬한 옅은 남색 판 — 사이트의 무대 느낌. 따로 그려서 섞어야 알파가 산다.
layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
ld = ImageDraw.Draw(layer)
ld.polygon([(720, 0), (W, 0), (W, H), (520, H)], fill=(76, 179, 239, 34))
ld.polygon([(760, 0), (W, 0), (W, H), (560, H)], fill=(76, 179, 239, 22))
bg.alpha_composite(layer)

# 일러: 높이에 맞춰 키우고 얼굴이 오도록 위쪽을 잡는다. 오른쪽 절반에 앉힌다.
scale = (H * 1.55) / art.height
a2 = art.resize((int(art.width * scale), int(art.height * scale)), Image.LANCZOS)
# 얼굴 중심이 세로 1/3 근처에 오게 — fbb(얼굴 상자)를 쓰지 않고 위 여백만 뗀다
x = W - a2.width + int(a2.width * 0.18)
y = -int(a2.height * 0.02)
bg.alpha_composite(a2, (x, y))

# 왼쪽 글자
def font(size, bold=False):
    for name in (["Pretendard-Bold.otf", "Pretendard-Bold.ttf"] if bold else ["Pretendard-Regular.otf"]) + ["malgunbd.ttf" if bold else "malgun.ttf", "arialbd.ttf" if bold else "arial.ttf"]:
        for base in (pathlib.Path(r"C:\Windows\Fonts"), pathlib.Path.home() / "AppData/Local/Microsoft/Windows/Fonts"):
            f = base / name
            if f.exists():
                return ImageFont.truetype(str(f), size)
    return ImageFont.load_default()

d = ImageDraw.Draw(bg)
d.text((72, 200), "DECK", font=font(96, True), fill=(255, 255, 255, 255))
d.text((72 + 310, 200), "LAB", font=font(96, True), fill=(76, 179, 239, 255))
d.text((76, 318), "NIKKE 덱 랩", font=font(40, True), fill=(238, 241, 246, 255))
d.text((76, 378), "솔로 · 유니온 레이드 딜 계산기", font=font(28), fill=(154, 163, 178, 255))
d.rectangle([(76, 440), (76 + 120, 444)], fill=(76, 179, 239, 255))
bg.convert("RGB").save(OUT, "PNG", optimize=True)
print("saved", OUT, OUT.stat().st_size // 1024, "KB")
