# Contributing

PromptLedger is an early-stage project. Issues and pull requests for reproducible bugs, clearer examples, and focused evaluation features are welcome. There is no response-time guarantee.

Use Python 3.11 or later. From the repository root:

```sh
python -m venv .venv
# Activate the environment using the command for your shell.
python -m pip install "setuptools>=68"
python -m pip install --no-build-isolation -e .
python -m unittest discover -s tests -v
python -m promptledger validate examples/maintainer-suite.json
python -m promptledger run examples/maintainer-suite.json --provider replay --responses examples/responses.good.json --output reports/local
```

Keep changes focused. Add a meaningful regression test when fixing behavior that could recur. Describe any suite-schema, report-schema, or command-line compatibility change in the pull request and changelog. Preserve the default local replay workflow and zero runtime dependency goal.

Use small, sanitized fixtures. Never commit API keys, private issue content, personal information, or confidential model outputs. Paid or live-provider calls must remain an explicit choice by the person running them; pull-request CI uses replay fixtures and needs no API secrets.

AI-assisted contributions are welcome. The contributor remains responsible for verifying generated code, sources, licenses, tests, and factual claims. Disclose substantial assistance when it helps reviewers understand the change. All contributions require human review before merging.

Before changing an evaluation baseline, explain the expected behavioral change and review the failing checks. Do not update fixtures only to make a failing run pass. The bundled examples are illustrative and must not be described as real model benchmarks.

For a suspected vulnerability, follow [the security policy](SECURITY.md). Project maintenance plans are in [docs/MAINTENANCE.md](docs/MAINTENANCE.md).

中文：欢迎提交可复现问题和范围清晰的 PR。请先运行测试，并删除提示词、输出和日志中的隐私信息及密钥。使用 AI 辅助不免除提交者的核验责任；修改基线时请说明行为变化的原因。
