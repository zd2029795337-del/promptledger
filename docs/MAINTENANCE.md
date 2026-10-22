# Maintenance plan

PromptLedger is a new open-source project for repeatable checks of AI assistant responses. Its initial examples are manually authored replay fixtures. They establish a reproducible development workflow, not real-world model performance or community adoption.

The current contribution workflow uses local development, unit tests, and GitHub Actions with replay data. Reports can be inspected before a maintainer accepts changes to checks or baselines. Project maintainers review and merge contributions; passing CI alone does not authorize a merge.

## Planned use of Codex

The following are future plans, not features already operating in this repository:

- Reproduce reported failures with small sanitized suites and propose focused fixes.
- Help triage issues by suggesting labels, asking for missing reproduction details, and linking related code for human review.
- Propose new checker implementations, edge-case tests, and documentation updates through reviewable pull requests.
- Draft release notes from merged changes, with a maintainer checking facts and deciding when to publish.
- Help review provider compatibility and baseline changes while keeping paid API calls opt-in.

Maintainers remain responsible for code review, release decisions, secrets, and claim accuracy. Any future use of provider credits will be an explicit maintainer choice, with a documented budget and task. Pull-request CI will continue to use local replay data unless the project deliberately reviews and documents a different workflow.

## Near-term priorities

Improve error messages and schema edge cases using reproducible reports; collect task-specific suites only with permission; and document the limits of deterministic checks. Potential provider integrations and additional check types should preserve the local workflow and should be evaluated before adoption.

## Evidence and applications

When describing this project in funding or access applications, state its actual age, maintainer role, repository visibility, and current features. Link the public repository and reproducible example commands. Do not describe synthetic replay data as live model results, forecast adoption as existing usage, or future Codex workflows as already deployed.

The repository may be useful as a concrete starting point for an application, but a new repository alone does not establish eligibility for any open-source support program.

中文：申请材料应如实说明这是新项目，示例回复为人工编写；Codex 用于问题分诊、修复和发布说明的内容属于后续计划。真实使用量、维护规模与历史贡献只能填写可核实的数据。
