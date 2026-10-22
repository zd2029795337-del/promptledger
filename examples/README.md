# Maintainer workflow example

These fixtures are **synthetic, manually authored responses**. They are not model outputs, measurements of a model, or evidence of deployment. Replay mode runs locally without an API key or network request.

The seven cases cover issue classification, release notes, untrusted issue text, a Chinese maintainer reply, exact workflow labels, structured breaking-change classification, and cautious handling of an unconfirmed security report. All six check types are represented. The injection example illustrates one forbidden string; it does not establish prompt-injection resistance.

Run from the repository root after installation:

```sh
python -m promptledger validate examples/maintainer-suite.json
python -m promptledger run examples/maintainer-suite.json --provider replay --responses examples/responses.good.json --output reports/baseline
python -m promptledger run examples/maintainer-suite.json --provider replay --responses examples/responses.regressed.json --baseline reports/baseline/report.json --output reports/current
```

The first run passes. The second run intentionally fails with exit code `1`: four cases have changed responses that violate checks. Inspect the generated JSON, Markdown, and HTML reports under `reports/current`. Keep `reports/baseline/report.json` to compare future runs against the passing result.

Passing the sample checks means only that the supplied response satisfied those checks. A real evaluation needs examples from your own task, representative failure cases, and human review. Changes to the suite should be reviewed alongside changes to its baseline.

中文说明：这些回复是人工编写的演示数据，不是真实模型测试结果。`good` 文件用于演示通过，`regressed` 文件用于演示失败与基线对比。正式评估应使用自己的任务与真实输出，避免把演示结果当作模型能力证明。
