import json
from pathlib import Path

path = Path(__file__).resolve().parent / "wos_response.json"
with path.open("r", encoding="utf-8") as stream:
    data = json.load(stream)

print("=== TOP LEVEL ===")
print(list(data.keys()))
print("\n=== METADATA ===")
print(json.dumps(data.get("metadata", {}), indent=2, ensure_ascii=False))
hits = data.get("hits", [])
print(f"\n=== HITS: {len(hits)} records ===")
if hits:
    first = hits[0]
    print("\n=== FIRST RECORD KEYS ===")
    for key in first:
        print(key)
    print("\n=== FIRST RECORD ===")
    print(json.dumps(first, indent=2, ensure_ascii=False))
