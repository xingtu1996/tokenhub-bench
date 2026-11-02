# AGENTS.md — tokenhub-bench

## Purpose and scope
Read `README.md` and the files relevant to the requested change. This repository has its own source-of-truth rules; do not import instructions from another repository implicitly.

## Local guardrails
Benchmark reproducibility is essential: pin model versions, prompts, datasets, runtime, pricing assumptions and evaluation settings. Do not claim rankings are current or statistically meaningful without source data and dates. Do not publish API keys or raw sensitive prompts.

## Shared Agent assets
- Shared reusable Skills: https://github.com/xingtu1996/xingtu-skills
- SDD method and validator: https://github.com/xingtu1996/xingtu-sdd
- Shared rules and hooks: xingtu-rules and xingtu-hooks.
- Cross-repository references are **not** automatic inheritance. Explicitly verify installation, supported scope and versions before use.

## Delivery workflow
1. Inspect existing instructions and source files; check for more specific directory-level guidance.
2. Use a small, reviewable SDD spec for meaningful behavior changes and define acceptance evidence first.
3. Make changes on a dedicated branch and open a Draft PR. Do not merge or publish automatically.
4. Run relevant checks only in a safe environment. Report the exact commands, CI links and outcomes; mark anything unrun as NOT_RUN.
5. Protect secrets, private Vault content and personal information. Preserve licenses and maintain a rollback path.

## Codex 本地仓与交付

- 行途标准本地仓为 `github/tokenhub-bench-restore/`。开工先核对 Git 根目录、工作区状态、`origin`、分支及上游；本地工作树是首选操作位置，不默认使用 `/tmp`。
- 按本仓基准复现要求先本地验证，再在隔离分支提交并推送；核对本地 HEAD、远端分支 SHA 和 PR head SHA 一致后，才报告同步完成。
- 已有同目标 PR 时先核对 base/head 和变更文件，复用适当分支，避免重复 PR；发现并发冲突先报告，不覆盖他人改动。PR 保持 Draft，禁止自动合并或发布。
- 指引默认中文；路径、命令、模型标识和协议字段保留原文。
