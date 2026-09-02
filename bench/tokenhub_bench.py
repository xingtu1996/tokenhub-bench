#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TokenHub 模型榜单引擎（编码实战横测 + 自动判分）
================================================
零强依赖（仅 Python 标准库 urllib），同一批编码任务并发跑全部模型，
自动执行测试用例计算「正确率」，输出速度 + 质量的综合榜单，并导出 CSV。

为什么是「榜单」而不是「横评」：
  单次横评是一次性快照；本脚本每次运行都带 batch_id 写入 CSV，
  多批次累积后即可观察模型水平随时间的变化 —— 这才是可持续的中文实测榜单。

用法：
  export TOKENHUB_API_KEY="你的TokenHub_API_Key"
  python tokenhub_bench.py                  # 跑全部 25 款 × 3 任务（带自动判分）
  python tokenhub_bench.py --model hy3      # 只测单款
  python tokenhub_bench.py --concurrency 8  # 并发数（默认 6）
  python tokenhub_bench.py --no-verify      # 只测速度，不跑判分（快）
  python tokenhub_bench.py --temperature 1  # 指定采样温度（部分模型仅允许 1）
  python tokenhub_bench.py --models-file models.json   # 从外部读模型清单（方便核对）
  python tokenhub_bench.py --prompt 自定义任务.txt      # 单任务快速测（跳过内置任务）

注意：模型名与 tokenhub_litellm_config.yaml 保持一致；标 [核对] 的以控制台为准。
模型若 404/400 会在结果里标 ❌，不影响其他模型。
"""
import os
import re
import sys
import csv
import json
import time
import uuid
import argparse
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import urllib.request
import urllib.error

API_KEY = os.environ.get("TOKENHUB_API_KEY")
BASE = "https://tokenhub.tencentmaas.com/v1"
if not API_KEY:
    sys.exit("❌ 请先设置环境变量： export TOKENHUB_API_KEY=\"你的Key\"")

# 与 litellm 配置一致的 25 款；[核对] 项请到控制台确认真实 model 字符串
DEFAULT_MODELS = [
    "hy3", "kimi-k3", "kimi-k2.7-code", "kimi-k2.7-code-highspeed",
    "minimax-m3", "minimax-m2.7",
    "deepseek-v4-pro-202606", "deepseek-v4-flash", "deepseek-v4-pro",
    "deepseek-v4-flash-202605",
    "glm-5.1", "glm-5.2", "glm-5", "glm-5-turbo", "glm-5v-turbo",
    "hy-mt2-pro", "hy-mt2-lite", "hy-mt2-plus",
    "mimo-v2.5-pro", "hy-role-latest", "hy-role",
    "qwen3.5-flash", "qwen3.5-plus",
    "kimi-k2.6", "kimi-k2.5",
]

# 内置编码任务：每个含 entry(约定函数名) + tests[(入参, 期望)] 用于自动判分。
# 入参若是单值直接 fn(arg)；若是元组则 fn(*args)。
TASKS = [
    {
        "id": "two_sum_unique",
        "entry": "two_sum_unique",
        "prompt": """你是一名资深 Python 工程师。请只输出代码（不要解释），实现函数：

def two_sum_unique(nums: list[int], target: int) -> list[list[int]]:
    \"\"\"返回所有和为 target 的不重复二元组；二元组内部升序，结果之间不重复，
    按在列表中首次出现的顺序返回。含完整类型注解与 docstring。\"\"\"

只输出代码。""",
        "tests": [
            (([2, 7, 11, 15], 9), [[2, 7]]),
            (([3, 3], 6), [[3, 3]]),
            (([1, 1, 2, 2], 3), [[1, 2]]),
            (([-1, 0, 1, 2, -1, -4], 0), [[-1, 1], [-1, 2]]),
        ],
    },
    {
        "id": "count_vowels",
        "entry": "count_vowels",
        "prompt": """请只输出代码（不要解释），实现函数：

def count_vowels(s: str) -> int:
    \"\"\"返回字符串中元音字母个数（a, e, i, o, u，不区分大小写）。含类型注解与 docstring。\"\"\"

只输出代码。""",
        "tests": [
            ("hello", 2), ("RHYTHM", 0), ("", 0), ("AEIOUaeiou", 10), ("Python3", 1),
        ],
    },
    {
        "id": "flatten",
        "entry": "flatten",
        "prompt": """请只输出代码（不要解释），实现函数：

def flatten(nested: list) -> list:
    \"\"\"展平任意层级嵌套的列表，顺序保持深度优先。含类型注解与 docstring。\"\"\"

只输出代码。""",
        "tests": [
            ([1, [2, [3, 4], 5]], [1, 2, 3, 4, 5]),
            ([], []), ([1, 2, 3], [1, 2, 3]), ([[[1]]], [1]),
            ([1, [2, [3, [4, [5]]]]], [1, 2, 3, 4, 5]),
        ],
    },
]


def extract_code(text: str) -> str:
    """从模型输出里尽量提取纯代码。优先代码块，否则尝试从 def 起截取。"""
    m = re.search(r"```(?:python)?\s*(.*?)```", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    idx = re.search(r"^\s*(def|class)\s", text, re.MULTILINE)
    if idx:
        return text[idx.start():].strip()
    return text.strip()


def verify(task: dict, code: str) -> tuple[int, int]:
    """执行模型生成的代码并跑内置测试用例，返回 (通过数, 总数)。"""
    ns: dict = {}
    try:
        exec(compile(code, "<model>", "exec"), ns)
    except Exception:
        return 0, len(task["tests"])
    fn = ns.get(task["entry"])
    if not callable(fn):
        return 0, len(task["tests"])
    passed = 0
    for args, expected in task["tests"]:
        try:
            got = fn(args) if not isinstance(args, tuple) else fn(*args)
            if got == expected:
                passed += 1
        except Exception:
            continue
    return passed, len(task["tests"])


def bench_one(model: str, prompt: str, temperature, task) -> dict:
    """对单个 (模型, 任务) 发一次请求，返回速度指标 + 该任务判分。"""
    url = f"{BASE}/chat/completions"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 900,
        "stream": True,
    }
    if temperature is not None:
        payload["temperature"] = temperature
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), method="POST",
        headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
    )
    t0 = time.time()
    first_tok = None
    text = ""
    out_tokens = None
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            if r.status != 200:
                return {"model": model, "ok": False, "error": f"HTTP {r.status}"}
            for raw in r:
                line = raw.decode("utf-8", "ignore").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                except Exception:
                    continue
                u = obj.get("usage")
                if u:
                    out_tokens = u.get("completion_tokens")
                choices = obj.get("choices") or []
                if choices:
                    delta = choices[0].get("delta", {})
                    # 推理模型常把代码放 reasoning_content，两者都累加以保底
                    piece = (delta.get("content") or "") + (delta.get("reasoning_content") or "")
                    if piece:
                        if first_tok is None:
                            first_tok = time.time()
                        text += piece
        t1 = time.time()
        res = {
            "model": model, "ok": True,
            "ttft_s": round(first_tok - t0, 2) if first_tok else None,
            "total_s": round(t1 - t0, 2),
            "out_tokens": out_tokens,
            "snippet": text[:70].replace("\n", " ").strip(),
        }
        if task is not None:
            code = extract_code(text)
            p, t = verify(task, code)
            res["passed"] = p
            res["tasks_total"] = t
        return res
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "ignore")[:160]
        return {"model": model, "ok": False, "error": f"HTTP {e.code}: {body}"}
    except Exception as e:
        return {"model": model, "ok": False, "error": str(e)[:160]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", help="只测指定 model（默认全部）")
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--no-verify", action="store_true", help="只测速度，不跑自动判分")
    ap.add_argument("--temperature", type=float, default=None,
                    help="采样温度（默认不传；Kimi 等模型仅允许 1）")
    ap.add_argument("--models-file", help="从 JSON 文件读取模型清单（数组或 {\"models\":[...]}）")
    ap.add_argument("--prompt", help="从文件读取单个自定义任务（跳过内置任务）")
    ap.add_argument("--tasks-only", action="store_true", help="仅列出内置任务后退出")
    args = ap.parse_args()

    if args.tasks_only:
        for t in TASKS:
            print(f"• {t['id']} (entry={t['entry']}, {len(t['tests'])} 用例)")
        return

    do_verify = not args.no_verify

    if args.models_file:
        with open(args.models_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        models = data["models"] if isinstance(data, dict) and "models" in data else data
    else:
        models = DEFAULT_MODELS

    # 单任务快速模式
    if args.prompt:
        with open(args.prompt, "r", encoding="utf-8") as f:
            single_prompt = f.read()
        single_task = {"id": "custom", "entry": "_custom", "prompt": single_prompt, "tests": []}
        jobs = [(m, single_task) for m in ([args.model] if args.model else models)]
        do_verify = False
        tasks_active = [single_task]
    else:
        tasks_active = TASKS if do_verify else [TASKS[0]]
        models = [args.model] if args.model else models
        jobs = [(m, t) if do_verify else (m, TASKS[0]) for m in models for t in tasks_active]

    # 按模型汇总
    agg = {m: {"ok": True, "ttft": [], "total": [], "out": [], "passed": 0,
               "tasks_total": 0, "snippet": "", "error": ""} for m in models}

    print(f"\n🚀 榜单引擎启动：{len(models)} 款模型 × {len(tasks_active)} 任务 | "
          f"判分={'开' if do_verify else '关'} | temp={args.temperature}\n")

    with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
        futures = {ex.submit(bench_one, m, t["prompt"], args.temperature,
                             (t if do_verify else None)): (m, t["id"]) for m, t in jobs}
        for fut in as_completed(futures):
            m, tid = futures[fut]
            res = fut.result()
            a = agg[m]
            if not res["ok"]:
                a["ok"] = False
                a["error"] = res.get("error", "")
            else:
                if res.get("ttft_s") is not None:
                    a["ttft"].append(res["ttft_s"])
                a["total"].append(res["total_s"])
                if res.get("out_tokens"):
                    a["out"].append(res["out_tokens"])
                a["snippet"] = res.get("snippet", "")
                if do_verify:
                    a["passed"] += res.get("passed", 0)
                    a["tasks_total"] += res.get("tasks_total", 0)

    results = []
    for m, a in agg.items():
        if a["ok"] and a["total"]:
            avg_ttft = round(sum(x for x in a["ttft"] if x) / max(1, len([x for x in a["ttft"] if x])), 2) \
                if any(a["ttft"]) else None
            avg_total = round(sum(a["total"]) / len(a["total"]), 2)
            avg_out = round(sum(a["out"]) / len(a["out"])) if a["out"] else None
            pr = round(a["passed"] / a["tasks_total"], 3) if a["tasks_total"] else 0.0
            results.append({"model": m, "ok": True, "ttft_s": avg_ttft, "total_s": avg_total,
                            "out_tokens": avg_out, "passed": a["passed"],
                            "tasks_total": a["tasks_total"], "pass_rate": pr,
                            "snippet": a["snippet"]})
        else:
            results.append({"model": m, "ok": False, "error": a["error"]})

    # 综合得分：质量(正确率) 70% + 速度(归一化) 30%
    valid = [r for r in results if r["ok"] and r.get("total_s")]
    totals = [r["total_s"] for r in valid] or [1]
    tmin, tmax = min(totals), max(totals)
    for r in results:
        if r["ok"] and r.get("total_s"):
            pr = r.get("pass_rate", 0) or 0
            speed_norm = 1.0 if tmax <= tmin else (tmax - r["total_s"]) / (tmax - tmin)
            r["score"] = round(pr * 70 + speed_norm * 30, 1)
        else:
            r["score"] = 0.0

    results.sort(key=lambda r: (not r["ok"], -r.get("score", 0), r.get("total_s") or 1e9))

    print("\n" + "=" * 96)
    print(f"{'排名':<4}{'模型':<28}{'得分':>6}{'正确率':>8}{'TTFT(s)':>9}{'总(s)':>9}{'出tok':>8}")
    print("-" * 96)
    for i, r in enumerate(results, 1):
        if r["ok"]:
            pr = f"{r.get('pass_rate', 0) * 100:.0f}%" if do_verify else "-"
            print(f"{i:<4}{r['model']:<28}{r['score']:>6}{pr:>8}"
                  f"{str(r['ttft_s']):>9}{str(r['total_s']):>9}{str(r['out_tokens']):>8}")
        else:
            print(f"{i:<4}{r['model']:<28}{'-':>6}{'-':>8}{'-':>9}{'-':>9}{'-':>8}  {r['error']}")
    print("=" * 96)

    batch_id = datetime.now().strftime("%Y%m%d") + "-" + uuid.uuid4().hex[:4]
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = f"benchmark_{ts}.csv"
    cols = ["batch_id", "rank", "model", "score", "pass_rate", "passed", "tasks_total",
            "ttft_s", "total_s", "out_tokens", "ok", "error"]
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for i, r in enumerate(results, 1):
            w.writerow({
                "batch_id": batch_id, "rank": i, "model": r["model"], "score": r.get("score", 0),
                "pass_rate": r.get("pass_rate", "") if do_verify else "",
                "passed": r.get("passed", "") if do_verify else "",
                "tasks_total": r.get("tasks_total", "") if do_verify else "",
                "ttft_s": r.get("ttft_s", ""), "total_s": r.get("total_s", ""),
                "out_tokens": r.get("out_tokens", ""), "ok": r["ok"], "error": r.get("error", ""),
            })
    print(f"\n📄 榜单明细已导出：{csv_path}  (batch_id={batch_id})\n")


if __name__ == "__main__":
    main()
