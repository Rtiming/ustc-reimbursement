"""Standalone review runtime: same implementation as assistant.py, no finance imports."""
import argparse
import json
import os
import sys
from pathlib import Path

# 接收方可能从普通Windows Python启动；JSON及后台子进程统一UTF-8。
os.environ['PYTHONUTF8']='1'
for stream in (sys.stdout,sys.stderr):
    if hasattr(stream,'reconfigure'):stream.reconfigure(encoding='utf-8')

sys.path.insert(0, str(Path(__file__).resolve().parent/'tools'))
from material_review_cli import configure, run

parser = argparse.ArgumentParser(description='材料核对工作台（经办接续包）')
configure(parser)
args = parser.parse_args()
if args.action in ('build', 'import-answers', 'import-workflow'):
    parser.error('接续包仅接续现有材料和网页记录；重建、冻结Excel读回和正式办理连接须使用完整工具仓及对应来源')
try:
    print(json.dumps(run(args), ensure_ascii=False, indent=2))
except (ValueError, OSError, ImportError) as exc:
    print(json.dumps({'ok': False, 'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
    sys.exit(1)
