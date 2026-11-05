"""通过stdin传合成JS和参数，避免Windows命令行长度改变既有测试覆盖。"""
import json
import subprocess
import shutil
import unittest

RUNNER = "const p=JSON.parse(require('node:fs').readFileSync(0,'utf8'));process.argv=[process.argv[0],...p.arguments];eval(p.source);"


def node_run(source, *arguments, node='node', **kwargs):
    if shutil.which(node) is None:
        raise unittest.SkipTest('Node开发测试运行时未安装；三系统CI继续执行此合同')
    return subprocess.run([node, '-e', RUNNER], input=json.dumps({'source': source, 'arguments': list(arguments)}, ensure_ascii=True), **kwargs)


def node_output(source, *arguments, node='node', **kwargs):
    if shutil.which(node) is None:
        raise unittest.SkipTest('Node开发测试运行时未安装；三系统CI继续执行此合同')
    return subprocess.check_output([node, '-e', RUNNER], input=json.dumps({'source': source, 'arguments': list(arguments)}, ensure_ascii=True), **kwargs)
