"""通过stdin传合成JS和参数，避免Windows命令行长度改变既有测试覆盖。"""
import json
import subprocess

RUNNER = "const p=JSON.parse(require('node:fs').readFileSync(0,'utf8'));process.argv=[process.argv[0],...p.arguments];eval(p.source);"


def node_run(source, *arguments, node='node', **kwargs):
    return subprocess.run([node, '-e', RUNNER], input=json.dumps({'source': source, 'arguments': list(arguments)}, ensure_ascii=True), **kwargs)


def node_output(source, *arguments, node='node', **kwargs):
    return subprocess.check_output([node, '-e', RUNNER], input=json.dumps({'source': source, 'arguments': list(arguments)}, ensure_ascii=True), **kwargs)
