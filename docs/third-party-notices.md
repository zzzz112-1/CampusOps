# RC14 原生扩展签名构建

在保留RC12两个签名DLL的基础上，仅为用户另一台电脑实际拒绝加载的_sqlite3.pyd和_uuid.pyd
采用同一已核验来源的原发行者签名CPython3.12扩展。解释器保持3.12.16；扩展构建版本3.12.14
及其兼容验证边界见runtime-fix-rc14.md、inputs/native-components-rc14.json。
PSF完整许可见docs/licenses/CPython-LICENSE.txt及runtime/LICENSE.txt，未移植或伪造签名，未宣称第三方背书。

# RC12 原生依赖签名构建（历史）

CPython保持3.12.16、SQLite保持3.53.1、Python依赖锁不变。为兼容Windows智能应用控制，仅更换libffi-8.dll和sqlite3.dll为已核验的原发行者签名构建，保留签名且不导入证书或改变系统信任。
实际来源为工具提供的OpenAI CodexPrimaryRuntime 26.1112.11011中的这两个开源库；不复制其他工具代码或任何商业项目内容。
libffi采用MIT许可，原文随包docs/licenses/libffi-LICENSE.txt；SQLite公有领域说明见docs/licenses/SQLite-NOTICE.txt。
固定哈希、签名证书和原始文件哈希见inputs/native-components-rc12.json。签名属于组件原发行者，并非CampusOps签名或第三方背书。

# RC7 新增依赖（历史）

本轮仅新增 PyOTP 2.9.0（MIT）与 qrcode 8.2（BSD），其许可文本随对应distribution保留在便携runtime内。
二维码使用已有Pillow在本机内存生成；不新增二维码云服务。DPAPI通过Windows标准API调用，无自制加密算法或第三方密钥服务。
以下原依赖和运行时许可记录继续适用，未批量升级。

# 第三方组件与许可说明 / RC6

本清单依据本项目D盘虚拟环境中锁定版本的原始distribution元数据生成。它是来源说明，不代替各组件完整许可。
便携运行时携带CPython的LICENSE.txt和依赖的dist-info许可/NOTICE等原文；完整复制时必须保留。
核心业务、规则校验、审批边界与编排实现的来源见项目文档。未声称自研Python、Streamlit、LangGraph或SDK。

CampusOps自身的公开发布与开源许可证尚待用户确认，本轮不会自动授予MIT/Apache或改变仓库可见性。
本项目仅使用虚构匿名样例，包内不含学生身份证明、真实学校数据或API密钥。

| 组件 | 锁定版本 | 原元数据声明 | 便携包内完整许可位置 |
| --- | --- | --- | --- |
| altair | 6.3.0 | Copyright (c) 2015-2025, Vega-Altair Developers All rights reserved. Redistribution and use in source and binary form...（完整正文见包内许可文件） | runtime/Lib/site-packages/altair-6.3.0.dist-info/licenses/LICENSE |
| annotated-types | 0.8.0 | MIT | runtime/Lib/site-packages/annotated_types-0.8.0.dist-info/licenses/LICENSE |
| anyio | 4.16.0 | MIT | runtime/Lib/site-packages/anyio-4.16.0.dist-info/licenses/LICENSE |
| attrs | 26.1.0 | MIT | runtime/Lib/site-packages/attrs-26.1.0.dist-info/licenses/LICENSE |
| certifi | 2026.10.23 | MPL-2.0 | runtime/Lib/site-packages/certifi-2026.10.23.dist-info/licenses/LICENSE |
| charset-normalizer | 3.5.2 | MIT | runtime/Lib/site-packages/charset_normalizer-3.5.2.dist-info/licenses/LICENSE |
| click | 8.5.0 | BSD-3-Clause | runtime/Lib/site-packages/click-8.5.0.dist-info/licenses/LICENSE.txt |
| colorama | 0.4.6 | License :: OSI Approved :: BSD License | runtime/Lib/site-packages/colorama-0.4.6.dist-info/licenses/LICENSE.txt |
| distro | 1.9.0 | Apache License, Version 2.0 | runtime/Lib/site-packages/distro-1.9.0.dist-info/LICENSE |
| h11 | 0.16.0 | MIT | runtime/Lib/site-packages/h11-0.16.0.dist-info/licenses/LICENSE.txt |
| httpcore | 1.0.9 | BSD-3-Clause | runtime/Lib/site-packages/httpcore-1.0.9.dist-info/licenses/LICENSE.md |
| httpcore2 | 2.16.0 | BSD-3-Clause | runtime/Lib/site-packages/httpcore2-2.16.0.dist-info/licenses/LICENSE.md |
| httpx | 0.28.1 | BSD-3-Clause | runtime/Lib/site-packages/httpx-0.28.1.dist-info/licenses/LICENSE.md |
| httpx2 | 2.16.0 | BSD-3-Clause | runtime/Lib/site-packages/httpx2-2.16.0.dist-info/licenses/LICENSE.md |
| idna | 3.20 | BSD-3-Clause | runtime/Lib/site-packages/idna-3.20.dist-info/licenses/LICENSE.md |
| iniconfig | 2.3.1 | MIT | runtime/Lib/site-packages/iniconfig-2.3.1.dist-info/licenses/LICENSE |
| itsdangerous | 2.2.0 | License :: OSI Approved :: BSD License | runtime/Lib/site-packages/itsdangerous-2.2.0.dist-info/LICENSE.txt |
| Jinja2 | 3.1.6 | License :: OSI Approved :: BSD License | runtime/Lib/site-packages/jinja2-3.1.6.dist-info/licenses/LICENSE.txt |
| jiter | 0.18.0 | MIT | runtime/Lib/site-packages/jiter-0.18.0.dist-info/licenses/LICENSE |
| jsonpatch | 1.42 | BSD-3-Clause | runtime/Lib/site-packages/jsonpatch-1.42.dist-info/licenses/AUTHORS<br>runtime/Lib/site-packages/jsonpatch-1.42.dist-info/licenses/LICENSE |
| jsonpointer | 3.8 | Modified BSD License | runtime/Lib/site-packages/jsonpointer-3.8.dist-info/licenses/AUTHORS<br>runtime/Lib/site-packages/jsonpointer-3.8.dist-info/licenses/LICENSE.txt |
| jsonschema | 4.26.0 | MIT | runtime/Lib/site-packages/jsonschema-4.26.0.dist-info/licenses/COPYING |
| jsonschema-specifications | 2025.9.1 | MIT | runtime/Lib/site-packages/jsonschema_specifications-2025.9.1.dist-info/licenses/COPYING |
| langchain-core | 1.7.6 | MIT | runtime/Lib/site-packages/langchain_core-1.7.6.dist-info/licenses/LICENSE |
| langchain-protocol | 0.0.20 | MIT | runtime/Lib/site-packages/langchain_protocol-0.0.20.dist-info/licenses/LICENSE |
| langgraph | 1.2.20 | MIT | runtime/Lib/site-packages/langgraph-1.2.20.dist-info/licenses/LICENSE |
| langgraph-checkpoint | 4.3.1 | MIT | runtime/Lib/site-packages/langgraph_checkpoint-4.3.1.dist-info/licenses/LICENSE |
| langgraph-prebuilt | 1.1.1 | MIT | runtime/Lib/site-packages/langgraph_prebuilt-1.1.1.dist-info/licenses/LICENSE |
| langgraph-sdk | 0.4.13 | MIT | runtime/Lib/site-packages/langgraph_sdk-0.4.13.dist-info/licenses/LICENSE |
| langsmith | 0.14.17 | MIT | runtime/Lib/site-packages/langsmith-0.14.17.dist-info/licenses/LICENSE |
| MarkupSafe | 3.0.4 | BSD-3-Clause | runtime/Lib/site-packages/markupsafe-3.0.4.dist-info/licenses/LICENSE.txt |
| narwhals | 2.28.0 | MIT | runtime/Lib/site-packages/narwhals-2.28.0.dist-info/licenses/LICENSE.md |
| numpy | 2.5.4 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | runtime/Lib/site-packages/numpy-2.5.4.dist-info/licenses/LICENSE.txt<br>runtime/Lib/site-packages/numpy-2.5.4.dist-info/licenses/numpy/_core/include/numpy/libdivide/LICENSE.txt<br>runtime/Lib/site-packages/numpy-2.5.4.dist-info/licenses/numpy/_core/src/common/pythoncapi-compat/COPYING<br>runtime/Lib/site-packages/numpy-2.5.4.dist-info/licenses/numpy/_core/src/highway/LICENSE<br>runtime/Lib/site-packages/numpy-2.5.4.dist-info/licenses/numpy/_core/src/multiarray/dragon4_LICENSE.txt<br>runtime/Lib/site-packages/numpy-2.5.4.dist-info/licenses/numpy/_core/src/npysort/x86-simd-sort/LICENSE.md<br>runtime/Lib/site-packages/numpy-2.5.4.dist-info/licenses/numpy/_core/src/umath/svml/LICENSE<br>runtime/Lib/site-packages/numpy-2.5.4.dist-info/licenses/numpy/fft/pocketfft/LICENSE.md<br>runtime/Lib/site-packages/numpy-2.5.4.dist-info/licenses/numpy/linalg/lapack_lite/LICENSE.txt<br>runtime/Lib/site-packages/numpy-2.5.4.dist-info/licenses/numpy/ma/LICENSE<br>runtime/Lib/site-packages/numpy-2.5.4.dist-info/licenses/numpy/random/LICENSE.md<br>runtime/Lib/site-packages/numpy-2.5.4.dist-info/licenses/numpy/random/src/distributions/LICENSE.md<br>runtime/Lib/site-packages/numpy-2.5.4.dist-info/licenses/numpy/random/src/mt19937/LICENSE.md<br>runtime/Lib/site-packages/numpy-2.5.4.dist-info/licenses/numpy/random/src/pcg64/LICENSE.md<br>runtime/Lib/site-packages/numpy-2.5.4.dist-info/licenses/numpy/random/src/philox/LICENSE.md<br>runtime/Lib/site-packages/numpy-2.5.4.dist-info/licenses/numpy/random/src/sfc64/LICENSE.md<br>runtime/Lib/site-packages/numpy-2.5.4.dist-info/licenses/numpy/random/src/splitmix64/LICENSE.md |
| openai | 2.54.0 | Apache-2.0 | runtime/Lib/site-packages/openai-2.54.0.dist-info/licenses/LICENSE |
| orjson | 3.13.0 | MPL-2.0 AND (Apache-2.0 OR MIT) | runtime/Lib/site-packages/orjson-3.13.0.dist-info/licenses/LICENSE-APACHE<br>runtime/Lib/site-packages/orjson-3.13.0.dist-info/licenses/LICENSE-MIT<br>runtime/Lib/site-packages/orjson-3.13.0.dist-info/licenses/LICENSE-MPL-2.0 |
| ormsgpack | 1.13.0 | Apache-2.0 OR MIT | runtime/Lib/site-packages/ormsgpack-1.13.0.dist-info/licenses/LICENSE-APACHE<br>runtime/Lib/site-packages/ormsgpack-1.13.0.dist-info/licenses/LICENSE-MIT |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause | runtime/Lib/site-packages/packaging-26.3.dist-info/licenses/LICENSE<br>runtime/Lib/site-packages/packaging-26.3.dist-info/licenses/LICENSE.APACHE<br>runtime/Lib/site-packages/packaging-26.3.dist-info/licenses/LICENSE.BSD |
| pandas | 3.1.1 | BSD-3-Clause | runtime/Lib/site-packages/pandas-3.1.1.dist-info/licenses/LICENSE<br>runtime/Lib/site-packages/pandas-3.1.1.dist-info/licenses/LICENSES/BOTTLENECK_LICENCE<br>runtime/Lib/site-packages/pandas-3.1.1.dist-info/licenses/LICENSES/DATEUTIL_LICENSE<br>runtime/Lib/site-packages/pandas-3.1.1.dist-info/licenses/LICENSES/HAVEN_LICENSE<br>runtime/Lib/site-packages/pandas-3.1.1.dist-info/licenses/LICENSES/HAVEN_MIT<br>runtime/Lib/site-packages/pandas-3.1.1.dist-info/licenses/LICENSES/KLIB_LICENSE<br>runtime/Lib/site-packages/pandas-3.1.1.dist-info/licenses/LICENSES/MUSL_LICENSE<br>runtime/Lib/site-packages/pandas-3.1.1.dist-info/licenses/LICENSES/NUMPY_LICENSE<br>runtime/Lib/site-packages/pandas-3.1.1.dist-info/licenses/LICENSES/PACKAGING_LICENSE<br>runtime/Lib/site-packages/pandas-3.1.1.dist-info/licenses/LICENSES/PSF_LICENSE<br>runtime/Lib/site-packages/pandas-3.1.1.dist-info/licenses/LICENSES/PYPERCLIP_LICENSE<br>runtime/Lib/site-packages/pandas-3.1.1.dist-info/licenses/LICENSES/PYUPGRADE_LICENSE<br>runtime/Lib/site-packages/pandas-3.1.1.dist-info/licenses/LICENSES/SAS7BDAT_LICENSE<br>runtime/Lib/site-packages/pandas-3.1.1.dist-info/licenses/LICENSES/STATSBASE_JL_LICENSE<br>runtime/Lib/site-packages/pandas-3.1.1.dist-info/licenses/LICENSES/ULTRAJSON_LICENSE<br>runtime/Lib/site-packages/pandas-3.1.1.dist-info/licenses/LICENSES/XSIMD_LICENSE<br>runtime/Lib/site-packages/pandas-3.1.1.dist-info/licenses/subprojects/fast_float-8.2.10/LICENSE-APACHE<br>runtime/Lib/site-packages/pandas-3.1.1.dist-info/licenses/subprojects/fast_float-8.2.10/LICENSE-BOOST<br>runtime/Lib/site-packages/pandas-3.1.1.dist-info/licenses/subprojects/fast_float-8.2.10/LICENSE-MIT |
| pillow | 13.0.0 | MIT-CMU | runtime/Lib/site-packages/pillow-13.0.0.dist-info/licenses/LICENSE |
| pip | 25.0.1 | MIT | runtime/Lib/site-packages/pip-25.0.1.dist-info/LICENSE.txt |
| pluggy | 1.7.0 | MIT | runtime/Lib/site-packages/pluggy-1.7.0.dist-info/licenses/LICENSE |
| protobuf | 7.36.2 | 3-Clause BSD License | runtime/Lib/site-packages/protobuf-7.36.2.dist-info/LICENSE |
| pyarrow | 26.0.1 | Apache-2.0 | runtime/Lib/site-packages/pyarrow-26.0.1.dist-info/licenses/LICENSE.txt<br>runtime/Lib/site-packages/pyarrow-26.0.1.dist-info/licenses/NOTICE.txt |
| pydantic | 2.14.3 | MIT | runtime/Lib/site-packages/pydantic-2.14.3.dist-info/licenses/LICENSE |
| pydantic_core | 2.50.4 | MIT | runtime/Lib/site-packages/pydantic_core-2.50.4.dist-info/licenses/LICENSE |
| pydeck | 0.9.3 | Apache License 2.0 | runtime/Lib/site-packages/pydeck-0.9.3.dist-info/licenses/LICENSE.txt |
| Pygments | 2.21.0 | BSD-2-Clause | runtime/Lib/site-packages/pygments-2.21.0.dist-info/licenses/AUTHORS<br>runtime/Lib/site-packages/pygments-2.21.0.dist-info/licenses/LICENSE |
| pytest | 9.2.0 | MIT | runtime/Lib/site-packages/pytest-9.2.0.dist-info/licenses/LICENSE |
| python-dateutil | 4.0.0 | BSD-3-Clause | runtime/Lib/site-packages/python_dateutil-4.0.0.dist-info/licenses/AUTHORS.md<br>runtime/Lib/site-packages/python_dateutil-4.0.0.dist-info/licenses/LICENSE |
| python-multipart | 0.0.33 | Apache-2.0 | runtime/Lib/site-packages/python_multipart-0.0.33.dist-info/licenses/LICENSE.txt |
| PyYAML | 6.0.3 | MIT | runtime/Lib/site-packages/pyyaml-6.0.3.dist-info/licenses/LICENSE |
| referencing | 0.37.0 | MIT | runtime/Lib/site-packages/referencing-0.37.0.dist-info/licenses/COPYING |
| requests | 2.34.2 | Apache-2.0 | runtime/Lib/site-packages/requests-2.34.2.dist-info/licenses/LICENSE<br>runtime/Lib/site-packages/requests-2.34.2.dist-info/licenses/NOTICE |
| requests-toolbelt | 1.0.0 | Apache 2.0 | runtime/Lib/site-packages/requests_toolbelt-1.0.0.dist-info/LICENSE |
| rpds-py | 2026.9.1 | MIT | runtime/Lib/site-packages/rpds_py-2026.9.1.dist-info/licenses/LICENSE |
| setuptools | 84.2.1 | MIT | runtime/Lib/site-packages/setuptools-84.2.1.dist-info/licenses/LICENSE<br>runtime/Lib/site-packages/setuptools/_vendor/autocommand-2.2.2.dist-info/LICENSE<br>runtime/Lib/site-packages/setuptools/_vendor/backports.tarfile-1.2.0.dist-info/LICENSE<br>runtime/Lib/site-packages/setuptools/_vendor/importlib_metadata-8.7.1.dist-info/licenses/LICENSE<br>runtime/Lib/site-packages/setuptools/_vendor/jaraco.text-4.0.0.dist-info/LICENSE<br>runtime/Lib/site-packages/setuptools/_vendor/jaraco_context-6.1.0.dist-info/licenses/LICENSE<br>runtime/Lib/site-packages/setuptools/_vendor/jaraco_functools-4.4.0.dist-info/licenses/LICENSE<br>runtime/Lib/site-packages/setuptools/_vendor/more_itertools-10.8.0.dist-info/licenses/LICENSE<br>runtime/Lib/site-packages/setuptools/_vendor/packaging-26.3.dist-info/licenses/LICENSE<br>runtime/Lib/site-packages/setuptools/_vendor/packaging-26.3.dist-info/licenses/LICENSE.APACHE<br>runtime/Lib/site-packages/setuptools/_vendor/packaging-26.3.dist-info/licenses/LICENSE.BSD<br>runtime/Lib/site-packages/setuptools/_vendor/platformdirs-4.4.0.dist-info/licenses/LICENSE<br>runtime/Lib/site-packages/setuptools/_vendor/tomli-2.4.0.dist-info/licenses/LICENSE<br>runtime/Lib/site-packages/setuptools/_vendor/wheel-0.46.3.dist-info/licenses/LICENSE.txt<br>runtime/Lib/site-packages/setuptools/_vendor/zipp-3.23.0.dist-info/licenses/LICENSE |
| sniffio | 1.3.1 | MIT OR Apache-2.0 | runtime/Lib/site-packages/sniffio-1.3.1.dist-info/LICENSE<br>runtime/Lib/site-packages/sniffio-1.3.1.dist-info/LICENSE.APACHE2<br>runtime/Lib/site-packages/sniffio-1.3.1.dist-info/LICENSE.MIT |
| starlette | 1.10.0 | BSD-3-Clause | runtime/Lib/site-packages/starlette-1.10.0.dist-info/licenses/LICENSE.md |
| streamlit | 1.66.1 | Apache-2.0 | 参见包内该distribution的METADATA；不推测未声明许可 |
| tenacity | 9.2.1 | Apache-2.0 | runtime/Lib/site-packages/tenacity-9.2.1.dist-info/licenses/LICENSE |
| tqdm | 4.70.1 | MPL-2.0 AND MIT | runtime/Lib/site-packages/tqdm-4.70.1.dist-info/licenses/LICENCE |
| truststore | 0.10.5 | MIT | runtime/Lib/site-packages/truststore-0.10.5.dist-info/licenses/LICENSE |
| typing-inspection | 0.4.5 | MIT | runtime/Lib/site-packages/typing_inspection-0.4.5.dist-info/licenses/LICENSE |
| typing_extensions | 4.16.0 | PSF-2.0 | runtime/Lib/site-packages/typing_extensions-4.16.0.dist-info/licenses/LICENSE |
| tzdata | 2026.6 | Apache-2.0 | runtime/Lib/site-packages/tzdata-2026.6.dist-info/licenses/LICENSE<br>runtime/Lib/site-packages/tzdata-2026.6.dist-info/licenses/licenses/LICENSE_APACHE |
| urllib3 | 2.8.0 | MIT | runtime/Lib/site-packages/urllib3-2.8.0.dist-info/licenses/LICENSE.txt |
| uuid_utils | 0.19.0 | BSD-3-Clause | runtime/Lib/site-packages/uuid_utils-0.19.0.dist-info/licenses/LICENSE.md |
| uvicorn | 0.54.0 | BSD-3-Clause | runtime/Lib/site-packages/uvicorn-0.54.0.dist-info/licenses/LICENSE.md |
| watchdog | 6.0.0 | Apache-2.0 | runtime/Lib/site-packages/watchdog-6.0.0.dist-info/COPYING<br>runtime/Lib/site-packages/watchdog-6.0.0.dist-info/LICENSE |
| websockets | 17.4 | BSD-3-Clause | runtime/Lib/site-packages/websockets-17.4.dist-info/licenses/LICENSE |
| wheel | 0.48.0 | MIT | runtime/Lib/site-packages/wheel-0.48.0.dist-info/licenses/LICENSE.txt |
| xxhash | 4.0.2 | BSD-2-Clause | runtime/Lib/site-packages/xxhash-4.0.2.dist-info/licenses/LICENSE |
| zstandard | 0.25.0 | BSD-3-Clause | runtime/Lib/site-packages/zstandard-0.25.0.dist-info/licenses/LICENSE |
