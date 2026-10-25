#!/usr/bin/env python3
"""静默诊断AppleEvent脚本传输尺寸；不读写发票或表单、不包含业务文件。"""
import argparse
import json
import subprocess
import sys
from pathlib import Path


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--tab-id', required=True)
    p.add_argument('--bytes', type=int, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    if not 1 <= a.bytes <= 300000:
        p.error('诊断填充限定1至300000字节')
    script = a.output.with_suffix('.js')
    if script.exists() or a.output.exists():
        p.error('保留原诊断，不覆盖或重发')
    source = '(()=>{const padding=' + json.dumps('x' * a.bytes) + ';return {ok:true,mode:"transport_size_probe",system_write:false,paddingBytes:padding.length};})()'
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(source)
    return subprocess.run([sys.executable, str(Path(__file__).with_name('edge_runtime.py')),
        str(script), '--tab-id', a.tab_id, '--operation-kind', 'read',
        '--output', str(a.output), '--quiet']).returncode


if __name__ == '__main__':
    raise SystemExit(main())
