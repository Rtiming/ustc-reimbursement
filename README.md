# USTC Reimbursement Toolkit

[中文](README.md) | [English](README.en.md)

当前版本为0.5.0-beta.2测试版，见[中英文发布说明](docs/releases/v0.5.0-beta.2.md)。Windows真实财务流程尚未实测；此测试版用于隔离安装、资料处理和接口试用，不代表真实批准、系统导出或5分钟整批目标已通过。旧0.4安装不会自动获得本版安全修复。

面向经办人和AI代理的报销辅助工具：材料收件与归档、PDF与OCR、可追溯的共用规则和项目专项、CLI/JSON与stdio MCP，以及中国科大财务网页适配器。

An agent-friendly reimbursement toolkit with local document intake, PDF/OCR,
scoped policy profiles, CLI/JSON and stdio MCP. The browser adapter targets USTC.
Independent community project; not an official university service.

## 下载与安装

从[Releases](https://github.com/Rtiming/ustc-reimbursement/releases)下载`ustc-reimbursement-*.zip`，解压到可写目录。请选择发行附件，GitHub自动生成的“Source code”压缩包主要供开发者使用。

| 系统 | 在解压目录执行 | 安装后自检 |
| --- | --- | --- |
| macOS | `sh install.sh`，或打开“安装与自检.command” | `sh install.sh --check` |
| Windows | 双击`install.cmd`，或在终端执行`install.cmd` | `install.cmd --check` |
| Linux | `sh install.sh` | `sh install.sh --check` |

首次联网安装本目录专属的Python和锁定依赖，默认不下载OCR模型；不需要提前安装Python，不改变全局PATH和AI客户端配置。需要扫描件识别时加`--with-ocr`。默认创建空白私人规范库，真实规则需要按单位要求配置。

安装报告在`.local/self-check-current.json`指向的HTML文件中。需要自己的资料目录和专项，例如：

```sh
sh install.sh --data-dir /绝对路径/报销资料 --profile-id project_alpha --title "项目甲" --mode student_activity
```

Windows将入口换成`install.cmd`，路径换成本机绝对路径。平台前提、中文路径、离线使用和排错见[各系统部署](docs/部署指南.md)，完整流程见[用户指南](docs/用户指南.md)。

## 交给AI使用

安装生成`.local/client-configs/mcp.json`，将该服务合并到支持stdio MCP的客户端。没有MCP的代理可以读取[AGENTS.md](AGENTS.md)，经同一CLI/JSON接口调用：

```json
{"method":"capabilities","params":{"intent":"materials"}}
```

将请求保存为JSON，再运行`sh tools/reimbursement.sh call --input /绝对路径/请求.json`；Windows使用`tools\reimbursement.cmd`。准确参数与MCP对应关系见`mcp/tool_catalog.json`。可直接让代理执行：“读取AGENTS.md，先完成材料和规则预检，按我指定的资料目录工作。”

## 能力与边界

| 能力 | 使用条件 |
| --- | --- |
| 材料归档、规则专项、离线计划、PDF | 本机安装及本单位有效规范 |
| 本地OCR | 显式`--with-ocr`；输出是待核对候选 |
| 远程OCR/总账 | 另外部署提供方，不依赖作者账号或服务器 |
| 科大财务网页 | 本机Edge接线、本人登录、逐动作实测；短信验证码本人填写 |
| 登记整批工作页 | macOS已有实现；Windows测试版已接统一接口，真实财务尚未实测；Linux不支持财务网页操作 |
| 手机采集/Excel成品 | 可选外部适配器，不包含其服务或专有运行时 |

查看明细PDF与原生列表导出是两个独立操作。安装自检只使用合成材料，不登录财务、不批准真实单据。实测范围见[验证说明](docs/验证说明.md)。

## 文档与参与

- [各系统部署与升级](docs/部署指南.md)
- [配置说明与语言入口](docs/配置说明.md)
- [新用户完整流程](docs/用户指南.md) · [模式与场景](docs/模式与场景.md) · [OCR统一流程](docs/OCR统一流程.md)
- [整批流程与登记](docs/整批流程与登记.md)
- [贡献与本地测试](CONTRIBUTING.md) · [安全报告](SECURITY.md) · [发布维护](docs/发布维护.md)

公开仓库只含源码、空白配置和合成测试。案件、私人规范、账号、Cookie、凭据与运行记录留在用户本机，不应上传Issue、PR或发布附件。

## 许可证

项目原创代码和文档采用[AGPL-3.0-or-later](LICENSE)，允许使用、修改和再分发，附带相应源码与许可义务。通过网络提供修改后的版本时应按许可证向使用者提供对应源码。依赖PyMuPDF采用AGPL/商业双许可；其他依赖和模型各自适用原许可，见[第三方说明](THIRD_PARTY_NOTICES.md)。

## 更新与重复部署

`sh update.sh`检查版本，`sh update.sh --install`核验后在新目录安装；Windows使用`update.cmd`。旧目录保留已有案件和恢复现场，新工作使用新MCP片段。没有更新入口的旧版先手动核SHA下载本版一次。

`sh install.sh --config /绝对路径/deployment.local.json --plan`只读预览部署；配置可从`distribution/deployment.example.json`复制。完整范围及案件迁移限制见[部署指南](docs/部署指南.md)。
