import json
import os
from pathlib import Path

import requests
from dotenv import load_dotenv


PROJECT = Path(__file__).resolve().parent
load_dotenv(PROJECT / ".env")
API_KEY = os.getenv("WOS_STARTER_API_KEY")
if not API_KEY:
    raise RuntimeError("没有找到 WOS_STARTER_API_KEY")

url = "https://api.clarivate.com/apis/wos-starter/v1/documents"
params = {
    "db": "WOS",
    "q": "TS=irrigation",
    "limit": 5,
    "page": 1,
    "detail": "full",
    "sortField": "PY+D",
}
try:
    response = requests.get(
        url,
        headers={"X-ApiKey": API_KEY},
        params=params,
        timeout=30,
        allow_redirects=False,
    )
except requests.RequestException as exc:
    raise SystemExit(str(exc).replace(API_KEY, "[REDACTED]")) from None

print("HTTP status:", response.status_code)
print("Request URL:", response.url.replace(API_KEY, "[REDACTED]"))
try:
    body = json.dumps(response.json(), ensure_ascii=False, indent=2)
except ValueError:
    body = response.text
body = body.replace(API_KEY, "[REDACTED]")
if response.status_code == 200:
    (PROJECT / "wos_response.json").write_text(body + "\n", encoding="utf-8")
print("Response preview:")
print(body[:4000])
if len(body) > 4000:
    print("... [显示前 4000 字符]")
if response.status_code != 200:
    raise SystemExit(1)
