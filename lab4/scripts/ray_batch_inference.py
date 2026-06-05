#!/usr/bin/env python3
"""
Lab 4 - Ray 多机批量推理任务调度
使用 Ray 将 prompt 分发给多台机器的 llama-server
支持串行、单机并行(ThreadPool)、Ray并行(轮询)、Ray并行(固定分配) 四种模式
"""

import ray
import requests
import time
import argparse
import json
import concurrent.futures
from datetime import datetime

# ===== 20 个测试 prompt =====
PROMPTS = [
    "请介绍一下大语言模型(LLM)的基本原理。",
    "用一句话总结:Transformer 架构基于注意力机制，摒弃了循环结构，实现了并行计算。",
    "解释以下 Python 代码：\ndef fibonacci(n):\n    a, b = 0, 1\n    for _ in range(n):\n        a, b = b, a + b\n    return a",
    "如果 3 只猫 3 分钟捉 3 只老鼠,100 只猫捉 100 只老鼠需要几分钟？",
    "在操作系统中，什么是上下文切换？为什么会有性能开销？",
    "请解释虚拟内存的工作原理。",
    "用中文写一首关于夏天的五言绝句。",
    "什么是死锁？产生死锁的四个必要条件是什么？",
    "请解释 TCP 三次握手的过程。",
    "排序算法中，快速排序和归并排序有什么区别？",
    "什么是 Docker?它和虚拟机有什么区别？",
    "请解释 HTTPS 和 HTTP 的区别。",
    "什么是 GPU 加速？在什么场景下适合使用？",
    "请用简单的语言解释什么是量子计算。",
    "解释操作系统中的进程和线程的区别。",
    "什么是 RESTful API?设计原则有哪些？",
    "请比较 C 语言和 Python 语言的主要区别。",
    "什么是缓存(Cache)?为什么缓存能提升系统性能？",
    "解释一下什么是编译器，它和解释器有什么区别？",
    "请解释 RAID 技术及其常见的几种级别。",
]


@ray.remote
class LLMInferenceActor:
    """Ray Actor:封装对一个 llama-server 节点的推理请求"""

    def __init__(self, server_url: str, name: str):
        self.server_url = server_url.rstrip("/")
        self.name = name
        self.total_requests = 0
        self.total_time = 0.0
        self.total_tokens = 0

    def infer(self, prompt: str, max_tokens: int = 128) -> dict:
        payload = {
            "prompt": f"<|im_start|>user\n{prompt}<|im_end|>\n<|im_start|>assistant",
            "n_predict": max_tokens,
            "temperature": 0.7,
            "stream": False,
        }
        start = time.time()
        try:
            resp = requests.post(
                f"{self.server_url}/completion",
                json=payload,
                timeout=120,
            )
            elapsed = time.time() - start
            if resp.status_code == 200:
                data = resp.json()
                content = data.get("content", "")
                n_tokens = data.get("tokens_predicted", len(content) // 2)
            else:
                content = f"ERROR: {resp.status_code}"
                n_tokens = 0
        except Exception as e:
            elapsed = time.time() - start
            content = f"EXCEPTION: {e}"
            n_tokens = 0

        self.total_requests += 1
        self.total_time += elapsed
        self.total_tokens += n_tokens

        return {
            "server": self.name,
            "prompt": prompt[:40] + "...",
            "start_time": round(start, 3),
            "end_time": round(start + elapsed, 3),
            "elapsed": round(elapsed, 3),
            "tokens": n_tokens,
            "output_len": len(content),
            "speed": round(n_tokens / elapsed, 2) if elapsed > 0 else 0,
            "output": content[:100],
        }

    def get_stats(self) -> dict:
        return {
            "server": self.name,
            "total_requests": self.total_requests,
            "total_time": round(self.total_time, 3),
            "total_tokens": self.total_tokens,
            "avg_latency": round(self.total_time / self.total_requests, 3) if self.total_requests else 0,
            "avg_speed": round(self.total_tokens / self.total_time, 2) if self.total_time else 0,
        }


def call_server(server_url, prompt, idx, max_tokens=128):
    """直接调用 llama-server (用于串行和ThreadPool模式)"""
    payload = {
        "prompt": f"<|im_start|>user\n{prompt}<|im_end|>\n<|im_start|>assistant",
        "n_predict": max_tokens,
        "temperature": 0.7,
        "stream": False,
    }
    start = time.time()
    try:
        resp = requests.post(f"{server_url}/completion", json=payload, timeout=120)
        elapsed = time.time() - start
        data = resp.json() if resp.status_code == 200 else {"content": "", "tokens_predicted": 0}
        content = data.get("content", "")
        n_tokens = data.get("tokens_predicted", len(content) // 2)
    except Exception as e:
        elapsed = time.time() - start
        content = f"EXCEPTION: {e}"
        n_tokens = 0

    return {
        "idx": idx,
        "server": server_url,
        "start_time": round(start, 3),
        "end_time": round(start + elapsed, 3),
        "elapsed": round(elapsed, 3),
        "tokens": n_tokens,
        "output_len": len(content),
        "speed": round(n_tokens / elapsed, 2) if elapsed > 0 else 0,
        "output": content[:100],
    }


def print_summary(title, results, print_detail=True):
    """打印执行结果汇总"""
    if print_detail:
        print(f"\n===== {title} =====")
        for r in results:
            print(f"  [{r['idx']}][{r.get('server','?')}] {r['elapsed']:.1f}s | {r['speed']:.1f} t/s | {r['tokens']} tok | output_len={r['output_len']}")

    total_time = sum(r["elapsed"] for r in results)
    total_tokens = sum(r["tokens"] for r in results)
    parallel_time = max(r["elapsed"] for r in results)
    latencies = sorted([r["elapsed"] for r in results])
    p50_idx = int(len(latencies) * 0.5)
    p95_idx = int(len(latencies) * 0.95)
    p50 = latencies[p50_idx]
    p95 = latencies[min(p95_idx, len(latencies) - 1)]
    failed = sum(1 for r in results if r["tokens"] == 0)
    print(f"\n{title} 统计:")
    print(f"  总耗时(串行累加): {total_time:.1f}s")
    print(f"  总耗时(并行/最大请求): {parallel_time:.1f}s")
    print(f"  总生成token: {total_tokens}")
    print(f"  平均延迟: {total_time/len(results):.2f}s/请求")
    print(f"  P50延迟: {p50:.2f}s")
    print(f"  P95延迟: {p95:.2f}s")
    print(f"  吞吐量: {len(results)/parallel_time:.2f} req/s")
    print(f"  平均生成速度: {total_tokens/parallel_time:.2f} t/s")
    print(f"  失败请求数: {failed}")


def serial_execution(server_url: str):
    """串行执行：所有 prompt 逐个发给一个 server"""
    results = []
    wall_start = time.time()
    for i, prompt in enumerate(PROMPTS):
        r = call_server(server_url, prompt, i + 1)
        results.append(r)
    wall_time = time.time() - wall_start

    print_summary("串行执行", results)
    return {
        "mode": "串行 (1 server)",
        "total_time": round(wall_time, 1),
        "total_time_sum": round(sum(r["elapsed"] for r in results), 1),
        "total_tokens": sum(r["tokens"] for r in results),
        "avg_latency": round(sum(r["elapsed"] for r in results) / len(results), 2),
        "throughput": round(len(results) / max(r["elapsed"] for r in results), 2),
        "avg_speed": round(sum(r["tokens"] for r in results) / max(r["elapsed"] for r in results), 2),
    }


def threadpool_parallel_execution(server_url: str, max_workers: int = 4):
    """单机并行：用 ThreadPoolExecutor 并发发请求到一台 server"""
    print(f"\n===== 单机并行 (ThreadPool, max_workers={max_workers}) =====")
    results = []
    wall_start = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(call_server, server_url, prompt, i + 1): i
            for i, prompt in enumerate(PROMPTS)
        }
        for future in concurrent.futures.as_completed(futures):
            r = future.result()
            results.append(r)
    wall_time = time.time() - wall_start

    results.sort(key=lambda x: x["idx"])
    for r in results:
        print(f"  [{r['idx']}][{r['server'][:30]}...] {r['elapsed']:.1f}s | {r['speed']:.1f} t/s | {r['tokens']} tok | output_len={r['output_len']}")

    total_tokens = sum(r["tokens"] for r in results)
    latencies = sorted([r["elapsed"] for r in results])
    p50 = latencies[int(len(latencies) * 0.5)]
    p95 = latencies[int(len(latencies) * 0.95)]
    failed = sum(1 for r in results if r["tokens"] == 0)
    print(f"\n单机并行 统计:")
    print(f"  墙钟耗时: {wall_time:.1f}s")
    print(f"  总生成token: {total_tokens}")
    print(f"  平均延迟: {sum(latencies)/len(latencies):.2f}s/请求")
    print(f"  P50延迟: {p50:.2f}s")
    print(f"  P95延迟: {p95:.2f}s")
    print(f"  吞吐量: {len(results)/wall_time:.2f} req/s")
    print(f"  平均生成速度: {total_tokens/wall_time:.2f} t/s")
    print(f"  失败请求数: {failed}")

    return {
        "mode": f"单机并行(ThreadPool-{max_workers})",
        "total_time": round(wall_time, 1),
        "total_tokens": total_tokens,
        "throughput": round(len(results) / wall_time, 2),
        "avg_speed": round(total_tokens / wall_time, 2),
    }


def ray_roundrobin_execution(server_urls: list):
    """Ray 并行：轮询分发到多台 server"""
    print(f"\n===== Ray 多机并行 - 轮询分发 ({len(server_urls)} 台) =====")
    actors = [LLMInferenceActor.remote(url, f"server{i+1}") for i, url in enumerate(server_urls)]

    futures = []
    for i, prompt in enumerate(PROMPTS):
        actor = actors[i % len(actors)]
        futures.append(actor.infer.remote(prompt))

    results = ray.get(futures)
    for r in results:
        print(f"  [{r['server']}] {r['elapsed']:.1f}s | {r['speed']:.1f} t/s | {r['tokens']} tok | start={r['start_time']}")

    stats = ray.get([a.get_stats.remote() for a in actors])
    for s in stats:
        print(f"\n  {s['server']}: {s['total_requests']}请求, "
              f"平均延迟{s['avg_latency']}s, 平均速度{s['avg_speed']} t/s")

    total_time = max(r["elapsed"] for r in results)
    total_tokens = sum(r["tokens"] for r in results)
    print(f"\nRay 轮询 统计:")
    print(f"  总耗时: {total_time:.1f}s")
    print(f"  总生成token: {total_tokens}")
    print(f"  吞吐量: {len(results)/total_time:.2f} req/s")
    print(f"  平均生成速度: {total_tokens/total_time:.2f} t/s")

    return {
        "mode": f"Ray 轮询 ({len(server_urls)}台)",
        "num_servers": len(server_urls),
        "total_time": round(total_time, 1),
        "total_tokens": total_tokens,
        "avg_latency": round(sum(r["elapsed"] for r in results) / len(results), 2),
        "throughput": round(len(results) / total_time, 2),
        "avg_speed": round(total_tokens / total_time, 2),
        "per_server": [
            {"server": s["server"], "requests": s["total_requests"],
             "avg_latency": s["avg_latency"], "avg_speed": s["avg_speed"]}
            for s in stats
        ],
    }


def ray_fixed_allocation_execution(server_urls: list):
    """Ray 并行：固定分配 - 前一半请求给 server1,后一半给 server2"""
    num_servers = len(server_urls)
    print(f"\n===== Ray 多机并行 - 固定分配 ({num_servers} 台) =====")
    actors = [LLMInferenceActor.remote(url, f"server{i+1}") for i, url in enumerate(server_urls)]

    futures = []
    for i, prompt in enumerate(PROMPTS):
        server_idx = 0 if i < len(PROMPTS) // 2 else 1
        if server_idx >= num_servers:
            server_idx = i % num_servers
        actor = actors[server_idx]
        futures.append(actor.infer.remote(prompt))

    results = ray.get(futures)
    for r in results:
        print(f"  [{r['server']}] {r['elapsed']:.1f}s | {r['speed']:.1f} t/s | {r['tokens']} tok | start={r['start_time']}")

    stats = ray.get([a.get_stats.remote() for a in actors])
    for s in stats:
        print(f"\n  {s['server']}: {s['total_requests']}请求, "
              f"平均延迟{s['avg_latency']}s, 平均速度{s['avg_speed']} t/s")

    total_time = max(r["elapsed"] for r in results)
    total_tokens = sum(r["tokens"] for r in results)
    print(f"\nRay 固定分配 统计:")
    print(f"  总耗时: {total_time:.1f}s")
    print(f"  总生成token: {total_tokens}")
    print(f"  吞吐量: {len(results)/total_time:.2f} req/s")
    print(f"  平均生成速度: {total_tokens/total_time:.2f} t/s")

    return {
        "mode": f"Ray 固定分配 ({num_servers}台)",
        "num_servers": num_servers,
        "total_time": round(total_time, 1),
        "total_tokens": total_tokens,
        "throughput": round(len(results) / total_time, 2),
        "avg_speed": round(total_tokens / total_time, 2),
    }


def ray_capacity_allocation_execution(server_urls: list, weights: list = None):
    """Ray 并行：按节点能力分配 - 根据权重分配请求"""
    num_servers = len(server_urls)
    if weights is None:
        weights = [1] * num_servers
    total_weight = sum(weights)
    print(f"\n===== Ray 多机并行 - 按能力分配 ({num_servers} 台, weights={weights}) =====")
    actors = [LLMInferenceActor.remote(url, f"server{i+1}") for i, url in enumerate(server_urls)]

    # 按权重分配: server_i 分配 weights[i]/total_weight 的请求
    allocations = []
    for i in range(num_servers):
        count = round(len(PROMPTS) * weights[i] / total_weight)
        allocations.append(count)
    # 调整使总数正确
    diff = len(PROMPTS) - sum(allocations)
    allocations[0] += diff

    server_idx = 0
    count_in_current = 0
    futures = []
    for i, prompt in enumerate(PROMPTS):
        futures.append(actors[server_idx].infer.remote(prompt))
        count_in_current += 1
        if count_in_current >= allocations[server_idx] and server_idx < num_servers - 1:
            server_idx += 1
            count_in_current = 0

    results = ray.get(futures)
    for r in results:
        print(f"  [{r['server']}] {r['elapsed']:.1f}s | {r['speed']:.1f} t/s | {r['tokens']} tok | start={r['start_time']}")

    stats = ray.get([a.get_stats.remote() for a in actors])
    for s in stats:
        print(f"\n  {s['server']}: {s['total_requests']}请求, "
              f"平均延迟{s['avg_latency']}s, 平均速度{s['avg_speed']} t/s")

    total_time = max(r["elapsed"] for r in results)
    total_tokens = sum(r["tokens"] for r in results)
    latencies = sorted([r["elapsed"] for r in results])
    p95 = latencies[int(len(latencies) * 0.95)]
    failed = sum(1 for r in results if r["tokens"] == 0)
    print(f"\nRay 按能力分配 统计:")
    print(f"  总耗时: {total_time:.1f}s")
    print(f"  总生成token: {total_tokens}")
    print(f"  吞吐量: {len(results)/total_time:.2f} req/s")
    print(f"  平均生成速度: {total_tokens/total_time:.2f} t/s")
    print(f"  P95延迟: {p95:.2f}s")
    print(f"  失败请求数: {failed}")

    return {
        "mode": f"Ray 按能力分配 ({num_servers}台, w={weights})",
        "num_servers": num_servers,
        "total_time": round(total_time, 1),
        "total_tokens": total_tokens,
        "throughput": round(len(results) / total_time, 2),
        "avg_speed": round(total_tokens / total_time, 2),
        "p95_latency": round(p95, 2),
        "failed": failed,
        "per_server": [
            {"server": s["server"], "requests": s["total_requests"],
             "avg_latency": s["avg_latency"], "avg_speed": s["avg_speed"]}
            for s in stats
        ],
    }


def main():
    parser = argparse.ArgumentParser(description="Ray 批量推理调度")
    parser.add_argument("--all", action="store_true", help="运行所有模式")
    parser.add_argument("--serial", action="store_true", help="仅串行测试")
    parser.add_argument("--threadpool", action="store_true", help="单机并行测试")
    parser.add_argument("--ray", action="store_true", help="Ray 并行测试")
    parser.add_argument("--fixed", action="store_true", help="固定分配测试")
    parser.add_argument("--workers", type=int, default=4, help="ThreadPool worker 数")
    parser.add_argument("--server1", default="http://localhost:8080", help="server1 URL")
    parser.add_argument("--server2", default=None, help="server2 URL")
    parser.add_argument("--server3", default=None, help="server3 URL")
    args = parser.parse_args()

    servers = [args.server1]
    if args.server2:
        servers.append(args.server2)
    if args.server3:
        servers.append(args.server3)

    do_all = args.all or not (args.serial or args.threadpool or args.ray or args.fixed)

    results = []

    if do_all or args.serial:
        print("=" * 60)
        r = serial_execution(args.server1)
        results.append(r)
        print(f"\n=== 串行结果摘要 ===")
        print(f"  总耗时: {r['total_time']}s")
        print(f"  吞吐: {r['throughput']} req/s")
        print(f"  生成速度: {r['avg_speed']} t/s")

    if do_all or args.threadpool:
        print("\n" + "=" * 60)
        r = threadpool_parallel_execution(args.server1, args.workers)
        results.append(r)
        print(f"\n=== 单机并行结果摘要 ===")
        print(f"  总耗时: {r['total_time']}s")
        print(f"  吞吐: {r['throughput']} req/s")
        print(f"  生成速度: {r['avg_speed']} t/s")

    if do_all or args.ray or args.fixed:
        if not do_all and not args.ray:
            ray.init(address="auto", ignore_reinit_error=True)
        elif do_all or args.ray or args.fixed:
            try:
                ray.init(address="auto", ignore_reinit_error=True)
            except Exception:
                ray.init(ignore_reinit_error=True)

    if do_all or args.ray:
        print("\n" + "=" * 60)
        r = ray_roundrobin_execution(servers)
        results.append(r)
        print(f"\n=== Ray 轮询结果摘要 ===")
        print(f"  总耗时: {r['total_time']}s")
        print(f"  吞吐: {r['throughput']} req/s")
        print(f"  生成速度: {r['avg_speed']} t/s")

    if do_all or args.fixed:
        print("\n" + "=" * 60)
        r = ray_fixed_allocation_execution(servers)
        results.append(r)
        print(f"\n=== Ray 固定分配结果摘要 ===")
        print(f"  总耗时: {r['total_time']}s")
        print(f"  吞吐: {r['throughput']} req/s")
        print(f"  生成速度: {r['avg_speed']} t/s")

    # 对比输出
    if len(results) >= 2:
        print("\n" + "=" * 65)
        print("                   性能对比总结")
        print("=" * 65)
        print(f"{'模式':<30} {'总耗时(s)':<12} {'吞吐(req/s)':<14} {'生成速度(t/s)':<14}")
        print("-" * 70)
        for r in results:
            print(f"{r['mode']:<30} {r['total_time']:<12} {r['throughput']:<14} {r['avg_speed']:<14}")

    return results


if __name__ == "__main__":
    main()