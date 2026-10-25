# 第三方组件与许可

项目原创代码与文档使用AGPL-3.0-or-later；完整文本在LICENSE，项目范围与版权声明在NOTICE。下表记录主要依赖的上游许可，不替代各安装包自带的LICENSE、COPYING、NOTICE或模型说明。

源码发行ZIP不包含第三方Python轮子、Python解释器、uv二进制、OCR权重、学校官方二进制表单或专有表格运行时。安装器按锁文件从公开来源获取依赖；另行制作含依赖的镜像、离线安装包或服务时，需保留对应第三方声明并履行其许可要求。

| 组件 | 锁定版本 | 上游许可与来源 |
| --- | --- | --- |
| PyMuPDF/MuPDF | PyMuPDF 1.27.2.3 | AGPL-3.0或Artifex商业许可；[官方许可说明](https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright)，安装包COPYING |
| Pillow | 12.2.0 | MIT-CMU；[项目LICENSE](https://github.com/python-pillow/Pillow/blob/main/LICENSE) |
| MCP Python SDK | 2.2.0 | MIT；[项目LICENSE](https://github.com/modelcontextprotocol/python-sdk/blob/main/LICENSE) |
| RapidOCR ONNX Runtime | 1.4.4 | Apache-2.0；[项目许可](https://github.com/RapidAI/RapidOCR/blob/main/LICENSE)，模型来源及SHA见OCR运行配置 |
| ONNX Runtime | 1.31.0 | MIT；[项目LICENSE](https://github.com/microsoft/onnxruntime/blob/main/LICENSE) |
| OpenCV Python | 5.0.0.93 | Apache-2.0；包含额外第三方组件，见安装包`cv2/LICENSE-3RD-PARTY.txt`及[上游许可](https://github.com/opencv/opencv-python/blob/master/LICENSE.txt) |
| NumPy | 2.5.4 | BSD-3-Clause及所含组件的0BSD、MIT、Zlib、CC0-1.0等；见安装包licenses与[项目LICENSE](https://github.com/numpy/numpy/blob/main/LICENSE.txt) |
| uv | 0.11.8 | MIT或Apache-2.0；[官方许可政策](https://docs.astral.sh/uv/policies/license/) |
| CPython独立构建 | 3.12.13 | PSF及构建所含组件许可；[Python许可](https://docs.python.org/3/license.html)与[python-build-standalone](https://github.com/astral-sh/python-build-standalone) |

其余直接和传递依赖完整版本在`requirements-mcp.lock.txt`、`requirements-materials.lock.txt`、`requirements-ocr.lock.txt`。许可清单见`distribution/third-party-packages.json`，来自这些锁定版本的包元数据；复杂表达式和内含组件须继续查安装包声明，元数据为空时不能推断为无许可限制。

OCR模型未随源码再分发，显式安装后对固定模型逐文件验证SHA。更换模型或自行再分发权重时，应另核对应模型版本的来源和许可，不能认为项目AGPL自动授予第三方权重权利。

`distribution/vendor/archive_file.py`是维护者自有共享归档程序的未修改快照，已在本项目公开授权范围内纳入AGPL-3.0-or-later；原来源、提交和SHA见`distribution/dependencies.json`。浏览器扩展manifest中的key是扩展标识用的公开密钥，不是账户私钥。

学校名称、网站、商标、表单与制度不因本仓库开源而转让或被重新许可。财务网站的使用权限和本单位有效规则由使用者自行取得。
