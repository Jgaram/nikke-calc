"""blablalink 프런트 번들을 전부 훑어 사용 중인 API 엔드포인트를 뽑아낸다."""
import json, re, sys, os, urllib.request, collections, pathlib

BASE = "https://www.blablalink.com"
ASSET = "/assets/nikke/version/default/assets/"
OUT = pathlib.Path(__file__).parent
CACHE = pathlib.Path(os.environ.get("BL_CACHE", OUT / "_chunks"))
CACHE.mkdir(parents=True, exist_ok=True)

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/151.0.0.0"}
ENTRY = ["/shiftyspad/union-raid", "/shiftyspad", "/unionrecruitment", "/", "/user"]


def get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def crawl():
    seen, queue = set(), collections.deque()
    for page in ENTRY:
        try:
            html = get(BASE + page)
        except Exception:
            continue
        for m in re.findall(r'(?:src|href)="([^"]*assets/[^"]*\.js)"', html):
            queue.append(m if m.startswith("http") else BASE + m)

    chunk_re = re.compile(r'["\'`]([\w./-]*?[\w-]+-[A-Za-z0-9_-]{6,10}\.js)["\'`]')
    while queue:
        url = queue.popleft()
        if url in seen:
            continue
        seen.add(url)
        name = url.split("/")[-1].split("?")[0]
        dest = CACHE / name
        if dest.exists():
            body = dest.read_text("utf-8", "replace")
        else:
            try:
                body = get(url)
            except Exception:
                continue
            dest.write_text(body, encoding="utf-8")
        for rel in chunk_re.findall(body):
            queue.append(BASE + ASSET + rel.split("/")[-1])
        print(f"\r{len(seen)} fetched, {len(queue)} queued", end="", file=sys.stderr)
    print(file=sys.stderr)
    return seen


def extract():
    pats = [
        re.compile(r'["\'`](/api/[A-Za-z0-9_/.\-]+)["\'`]'),
        re.compile(r'["\'`]((?:proxy|direct)/[A-Za-z0-9_]+/[A-Za-z0-9_]+)["\'`]'),
        re.compile(r'["\'`]([a-z]+/(?:proxy|direct)/[A-Za-z0-9_]+/[A-Za-z0-9_]+)["\'`]'),
    ]
    found = collections.defaultdict(set)
    for f in CACHE.glob("*.js"):
        body = f.read_text("utf-8", "replace")
        for p in pats:
            for hit in p.findall(body):
                found[hit].add(f.name)
    return found


if __name__ == "__main__":
    if "--extract-only" not in sys.argv:
        crawl()
    found = extract()
    data = {
        "chunks": len(list(CACHE.glob("*.js"))),
        "endpoints": {k: sorted(v) for k, v in sorted(found.items())},
    }
    (OUT / "api_inventory.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"chunks={data['chunks']} endpoints={len(found)}")
