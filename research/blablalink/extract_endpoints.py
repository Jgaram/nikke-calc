"""v4 API 모듈에서 base 팩토리와 엔드포인트 호출을 짝지어 전체 목록을 복원한다."""
import json, os, re, pathlib, collections

CACHE = pathlib.Path(os.environ.get("BL_CACHE", "_chunks"))
OUT = pathlib.Path(__file__).parent

base_def = re.compile(r"([A-Za-z_$][\w$]*)\s*=\s*[A-Za-z_$][\w$]*\(`(/[A-Za-z0-9_/.\-]*(?:proxy|direct|act|lip)[A-Za-z0-9_/.\-]*)`\)")
call = re.compile(r"([A-Za-z_$][\w$]*)\s*=\s*([A-Za-z_$][\w$]*)\(`(/[A-Za-z0-9_/.\-]+)`\)")

rows = []
for f in sorted(CACHE.glob("*.js")):
    if "legacy" in f.name:
        continue
    src = f.read_text("utf-8", "replace")
    if "proxy/Game" not in src and "proxy/standalonesite" not in src:
        continue
    bases = {m.group(1): m.group(2) for m in base_def.finditer(src)}
    if not bases:
        continue
    for m in call.finditer(src):
        var, fn, path = m.group(1), m.group(2), m.group(3)
        if fn in bases and path.count("/") <= 3:
            rows.append({"chunk": f.name, "endpoint": "/api" + bases[fn] + path})

uniq = sorted({r["endpoint"] for r in rows})
by_svc = collections.defaultdict(list)
for e in uniq:
    parts = e.split("/")
    by_svc["/".join(parts[:5])].append(parts[-1])

data = {"total": len(uniq), "groups": {k: sorted(v) for k, v in sorted(by_svc.items())}}
(OUT / "api_endpoints.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
print("total:", len(uniq))
for k, v in sorted(by_svc.items()):
    print(f"  {k}  ({len(v)})")
