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

## 能力

- 多模型横向对比（质量 / 速度 / Token 消耗）
- 可复现：固定 prompt 集 + 固定指标 + 自动判分
- 输出结构化对比报告（CSV）

## 已含内容

| 文件 | 说明 |
|------|------|
| bench/tokenhub_bench.py | 评测引擎：多模型 × 多任务，自动判分 |
| bench/tokenhub_minigate.py | 轻量网关代理（多模型路由）|
| bench/tokenhub_litellm_config.yaml | litellm 配置示例（key 走环境变量）|
| results/benchmark_*.csv | 真实评测结果样例（hy3 / deepseek-v4-flash / kimi-k3 等）|

> 评测结果样例：hy3 编码 95 分、deepseek-v4-flash 73.7 分、kimi-k3 50 分（pass_rate / ttft / 输出 token 均有记录）——直接用于模型选型参考。

## 目录结构

```
bench/       # 评测引擎 + 配置
results/     # 评测结果（CSV）
```

## 许可证

MIT License
