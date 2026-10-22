# PromptLedger

**面向 AI 提示词的轻量回归测试工具：离线重放、基线对比、三种报告、CI 门禁。**

[English](README.md) · [配置格式](docs/FORMAT.md) · [贡献指南](CONTRIBUTING.md) · [MIT 许可证](LICENSE)

修改提示词、升级模型之后，分类输出还符合 JSON 格式吗？版本说明有没有增加没有依据的承诺？PromptLedger 把这些要求写成可检查的案例，生成报告，并用退出码告诉 CI 是否通过。

项目处于初始版本，使用 Python 3.11+，运行时没有第三方依赖。仓库样例是人工编写的合成回复，用来展示工具行为，不是真实模型性能数据。

## 不用 API Key 即可运行

在项目根目录执行：

```sh
python -m promptledger validate examples/maintainer-suite.json
python -m promptledger run examples/maintainer-suite.json --responses examples/responses.good.json --output reports/baseline
```

预期：7/7 通过，不访问网络。浏览器打开 `reports/baseline/report.html`，也可查看 Markdown 和 JSON 报告。

再运行一组故意包含错误的回复：

```sh
python -m promptledger run examples/maintainer-suite.json --responses examples/responses.regressed.json --baseline reports/baseline/report.json --output reports/current
```

预期：3/7 通过，发现 4 个退化案例，退出码为 `1`。这是演示中预期的测试失败。

可选安装命令行入口：

```sh
python -m pip install -e .
promptledger --help
```

## 已实现的功能

- 文本包含、文本禁止、完全匹配、严格 JSON、JSON 指针值比较、字符长度，共 6 类检查。
- 离线重放回复；显式选择 OpenAI 时调用 Responses API。
- JSON、Markdown、离线 HTML 报告，包含每条回复、检查结果、耗时及成功响应的 token 用量。
- 基线对比：识别退化、改善、新增、删除、输出变化和检查规则变化。
- 完整配置预检；基线案例被删除或检查规则改变时，在生成回复前要求审阅并重建基线。
- 单元测试和 Windows/Linux GitHub Actions；默认 CI 不使用密钥、不调用付费 API。

报告保留规范化的测试配置快照及摘要，便于追溯指令和预期条件。案例 ID 用于关联前后版本；提示词、总指令和模型可以变化，检查规则变化需要建立新基线。

## 可选 OpenAI 调用

PowerShell 设置环境变量后执行，模型 ID 使用你实际有权访问的型号：

```powershell
$env:OPENAI_API_KEY = "YOUR_API_KEY"
$env:OPENAI_MODEL = "YOUR_AVAILABLE_MODEL_ID"
python -m promptledger run examples/maintainer-suite.json --provider openai --output reports/live --max-output-tokens 1024 --max-cases 10
```

选择 OpenAI 会把配置中的指令和案例提示词发送到 OpenAI。每个案例请求一次，不自动重试，设置 `store: false`，不启用工具调用。输出 token 上限包含推理 token，不等于金额预算；失败或超时的请求也可能产生费用。统计只覆盖成功解析的响应。

报告包含提示词、回复及完整配置快照，公开前请检查其中的数据。离线重放不会发送数据；工具不读取 `.env` 文件，也不执行模型输出。付费接口本次开发仅做模拟测试，实际模型访问和结果需在配置你自己的 API 账号后验证。

## 维护与限制

```sh
python -m unittest discover -s tests -v
```

退出码：`0` 通过，`1` 检查失败或基线门禁失败，`2` 配置/文件错误，`130` 用户中断。`diff` 只比较变化，原来就失败且没有变化的案例不会单独阻止它通过；需要全部通过时使用 `run`。

项目不提供语义判分器、托管平台或自动发表评论。关键词检查不能证明事实正确，也不能证明抵御提示注入；样例中的相关案例仅展示显式断言的用法。具体格式见[配置文档](docs/FORMAT.md)，后续计划见[维护说明](docs/MAINTENANCE.md)。
