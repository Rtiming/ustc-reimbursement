# 参与开发

公开协作、Issue、Pull Request与版本发布均在[本仓库](https://github.com/Rtiming/ustc-reimbursement)。只提交通用实现、空白模板和合成测试；真实案件、规则来源中的个人信息、浏览器现场、账号配置与日志不进入Git。

## 本地流程

安装方式见[部署指南](docs/部署指南.md)。安装后，使用`.local/mcp-venv/bin/python`，Windows改为`.local/mcp-venv/Scripts/python.exe`：

```sh
.local/mcp-venv/bin/python -m unittest discover -s tests -p 'test_*.py'
.local/mcp-venv/bin/python tools/distribution_smoke.py --mcp --materials --output .local/dev-smoke.json
```

OCR修改需要显式安装`--with-ocr`，再给演练加`--ocr`。所有测试使用合成材料，不允许在CI登录学校系统、采集真实票据或发送批准操作。新增单位适配要独立登记其真实验收范围。

## 文件职责

- `assistant.py`、`tools/assistant_contract.py`、`tools/assistant_api.py`维护统一入口和固定接口；MCP与CLI复用同一合同。
- `tools/reimbursement_standards.py`负责私人规则库；`tools/workspace_config.py`负责本机绑定；OCR内核与任务流程分开维护。
- `distribution/source-files.json`是发行源码的明确允许清单，新增公共文件必须登记。构建器不扫描打包工作树。
- `distribution/README.md`、`AGENTS.md`、`用户指南.md`等是公开入口的编辑正本；`tools/build_distribution.py`的ALIASES列出派生到根目录或docs的副本。

修改派生入口应先编辑distribution正本，再运行：

```sh
.local/mcp-venv/bin/python tools/prepare_public_checkout.py
.local/mcp-venv/bin/python tools/prepare_public_checkout.py --execute
```

这个命令只同步声明的公共别名，不修改用户资料。`MANIFEST.json`属于发行包，不提交到源码分支；Git检出自检和发行包SHA自检分别留证。

提交前检查`git diff`与暂存文件，逐项暂存，不添加cases、materials、.local、规范库或凭据。使用合成例子复现问题。PR说明具体触发、改后行为、运行的验证和未验收平台；对未知财务写结果不增加自动重试。

贡献内容按本项目AGPL-3.0-or-later发布；外来代码需记录原作者、来源和许可，不能仅贴上本项目许可。依赖变更同步锁文件、第三方说明与相关平台验收。
