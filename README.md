# TokenHub Bench · 可复现 LLM 模型榜单评测

> 可复现的模型能力对比评测工具，量化模型在真实任务上的表现。

![MIT](https://img.shields.io/badge/license-MIT-green.svg)

## 🎯 这是什么

`tokenhub-bench` 是行途开源矩阵的**模型评测资产**。提供可复现的 LLM 能力对比基准——在同一套任务、同一套指标下横向评测多模型，输出量化对比结果。

## 🚀 快速开始

```bash
python3 tokenhub_bench.py --model A --model B --task coding
python3 tokenhub_minigate.py   # 轻量版
```

## 📊 能力

- 多模型横向对比（质量 / 速度 / Token 消耗）
- 可复现：固定 prompt 集 + 固定指标
- 输出结构化对比报告

## 📄 许可证

MIT License

---

> AI 辅助创作 · 内容基于真实工程实践

## 📁 目录结构

```
bench/       # 评测任务与基准
results/     # 评测结果
```

## 🗺 Roadmap

- [ ] 评测任务集 + 指标定义
- [ ] 首批模型对比结果
