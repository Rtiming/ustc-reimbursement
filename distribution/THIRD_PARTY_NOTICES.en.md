# Third-party components

[English](THIRD_PARTY_NOTICES.en.md) | [中文](THIRD_PARTY_NOTICES.md)

Original project code/documentation uses AGPL-3.0-or-later; see LICENSE and NOTICE. Third-party packages, runtimes and models retain their own terms. This inventory does not replace the LICENSE/COPYING/NOTICE files included with their exact distributions.

The source ZIP contains no third-party wheels, Python runtime, uv binary, OCR weights, official university binary forms or proprietary spreadsheet runtime. Installation downloads pinned public packages. Redistributing a bundled image/offline runtime requires preserving the relevant notices and meeting each component's obligations.

| Component | Pinned version | Upstream license / source |
| --- | --- | --- |
| PyMuPDF/MuPDF | PyMuPDF 1.27.2.3 | AGPL or Artifex commercial; [official licensing](https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright) |
| Pillow | 13.0.0 | MIT-CMU; [LICENSE](https://github.com/python-pillow/Pillow/blob/main/LICENSE) |
| defusedxml | 0.7.1 | PSF; [project](https://github.com/tiran/defusedxml) |
| MCP Python SDK | 2.2.0 | MIT; [LICENSE](https://github.com/modelcontextprotocol/python-sdk/blob/main/LICENSE) |
| RapidOCR ONNX Runtime | 1.4.4 | Apache-2.0; [project](https://github.com/RapidAI/RapidOCR) |
| ONNX Runtime | 1.31.0 | MIT; [LICENSE](https://github.com/microsoft/onnxruntime/blob/main/LICENSE) |
| OpenCV Python | 5.0.0.93 | Apache-2.0 plus bundled third-party notices; [project](https://github.com/opencv/opencv-python) |
| NumPy | 2.5.4 | BSD-3-Clause and included-component licenses; [LICENSE](https://github.com/numpy/numpy/blob/main/LICENSE.txt) |
| uv | 0.13.5 | MIT or Apache-2.0; [policy](https://docs.astral.sh/uv/policies/license/) |
| CPython standalone build | 3.12.15 | PSF and included components; [Python license](https://docs.python.org/3/license.html), [build project](https://github.com/astral-sh/python-build-standalone) |

All direct/transitive Python versions are in the three requirement lockfiles. `distribution/third-party-packages.json` records package metadata for those exact installed versions. Missing or simplified metadata is not permission to ignore bundled components or their original notices.

OCR weights are not redistributed in source. Explicit installation verifies their frozen hashes. A changed model or a separately redistributed weight package requires its own provenance/license check.

`distribution/vendor/archive_file.py` is the maintainer's unchanged shared archive-helper snapshot, included under the project license. The adapter verifies and executes those exact bytes; source identity and hashes are in `distribution/dependencies.json`. The extension manifest key is a public extension-identity key, not an account credential.

University names, websites, marks, forms and rules are not relicensed by this repository. Users must independently obtain system access and applicable policy authority.
