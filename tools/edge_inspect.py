#!/usr/bin/env python3
"""通过 Edge 官方 AppleScript 接口读取财务标签页元数据，不做页面操作。"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output", type=Path, default=ROOT / "evidence/edge_metadata.json")
    args = ap.parse_args()
    js = (ROOT / "tools/edge_inspect.js").read_text()
    if sys.platform == 'win32':
        from edge_read_probe import evaluate, list_tabs
        try:
            rows = [json.loads(evaluate(js, tab)) for tab in list_tabs()]
        except (RuntimeError, TimeoutError, ValueError) as error:
            print(json.dumps({'ok': False, 'reason': str(error)}, ensure_ascii=False))
            return 1
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps({'tabs': rows}, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        print(json.dumps({'ok': True, 'tab_count': len(rows), 'page_count': sum(len(r['pages']) for r in rows), 'output': str(args.output)}, ensure_ascii=False))
        return 0 if rows else 1
    # AppleScript string literal; no shell interpolation of JavaScript or browser data.
    literal = json.dumps(js, ensure_ascii=False)
    script = '''with timeout of 20 seconds
tell application "Microsoft Edge"
set rows to {}
repeat with w in windows
repeat with t in tabs of w
if URL of t contains "https://cwzh.ustc.edu.cn/YBX/main.jsp" then
set end of rows to (execute t javascript %s)
end if
end repeat
end repeat
set AppleScript's text item delimiters to linefeed
return rows as text
end tell
end timeout''' % literal
    try:
        result = subprocess.run(["/usr/bin/osascript", "-"], input=script, text=True,
                                capture_output=True, timeout=25)
    except subprocess.TimeoutExpired:
        print(json.dumps({"ok": False, "reason": "Edge script inspection timed out"}))
        return 1
    if result.returncode:
        print(json.dumps({"ok": False, "reason": result.stderr.strip()}, ensure_ascii=False, indent=2))
        return 1
    rows = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"tabs": rows}, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"ok": True, "tab_count": len(rows), "page_count": sum(len(r["pages"]) for r in rows),
                      "output": str(args.output)}, ensure_ascii=False, indent=2))
    return 0 if rows else 1


if __name__ == "__main__":
    raise SystemExit(main())
