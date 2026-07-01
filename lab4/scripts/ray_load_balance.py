#!/usr/bin/env python3
"""
Lab 4 - Ray 选做：负载均衡调度对比
对比默认调度与自定义吞吐量感知调度在各节点的任务分配
和吞吐量差异，输出 load_balance_result.txt。

参数:
  --num-workers    工作节点数 (默认: 2)
  --batch-size     每批推理请求数 (默认: 8)
  --duration       压力测试持续时间/秒 (默认: 60)
"""
策略1: 轮询 (round-robin)
策略2: 按历史平均延迟分配 (latency-aware)

使用方式（先启动两个 llama-server）:
  python3 ray_load_balance.py
"""

import requests
import time
import statistics

SERVER1 = "http://127.0.0.1:8080"
SERVER2 = "http://127.0.0.1:8081"

PROMPTS = [
    # 中文知识问答 (6)
    "请介绍一下大语言模型(LLM)的基本原理。",
    "请解释什么是量子计算。",
    "什么是 Docker？它和虚拟机有什么区别？",
    "请解释什么是 RESTful API。",
    "什么是微服务架构？",
    "什么是区块链技术？请用简单语言解释。",

    # 技术概念解释 (6)
    "请解释虚拟内存的工作原理。",
    "什么是死锁？产生死锁的四个必要条件是什么？",
    "在操作系统中，什么是上下文切换？为什么会有性能开销？",
    "什么是 GPU 加速？在什么场景下适合使用？",
    "什么是缓存(Cache)？为什么缓存能提升系统性能？",
    "什么是数据库索引？它如何提高查询性能？",

    # 代码解释 (4)
    "解释以下 Python 代码：\ndef fibonacci(n):\n    a, b = 0, 1\n    for _ in range(n):\n        a, b = b, a + b\n    return a",
    "请比较 C 语言和 Python 语言的主要区别。",
    "解释一下什么是编译器，它和解释器有什么区别？",
    "请解释什么是 Big O 表示法，并举例说明。",

    # 逻辑推理 (4)
    "如果 3 只猫 3 分钟捉 3 只老鼠，100 只猫捉 100 只老鼠需要几分钟？",
    "请解释 TCP 三次握手的过程。",
    "请解释 HTTPS 和 HTTP 的区别。",
    "什么是负载均衡？常见的调度算法有哪些？",

    # 摘要/总结 (4)
    "用一句话总结：Transformer 架构基于注意力机制，摒弃了循环结构，实现了并行计算。",
    "排序算法中，快速排序和归并排序有什么区别？",
    "请解释 HTTP 状态码 200、404、500 的区别。",
    "什么是 DevOps？核心原则有哪些？",

    # 系统/网络 (6)
    "解释操作系统中的进程和线程的区别。",
    "请解释 RAID 技术及其常见的几种级别。",
    "什么是 CDN？它是如何加速网站访问的？",
    "什么是容器化技术？Docker 是如何工作的？",
    "什么是机器学习中的过拟合？如何避免？",
    "请解释什么是 NoSQL 数据库，它和关系型数据库有什么区别？",
]


def call_server(server_url, prompt, idx, max_tokens=128):
    """向 llama-server 发一个推理请求，返回结果字典"""
    payload = {
        "prompt": f"<|im_start|>user\n{prompt}<|im_end|>\n<|im_start|>assistant",
        "n_predict": max_tokens,
        "temperature": 0.7,
        "stream": False,
    }
    start = time.time()
    try:
        resp = requests.post(f"{server_url}/completion", json=payload, timeout=300)
        elapsed = time.time() - start
        if resp.status_code == 200:
            data = resp.json()
            content = data.get("content", "")
            n_tokens = data.get("tokens_predicted", len(content) // 2)
            return {"idx": idx, "elapsed": round(elapsed, 3), "tokens": n_tokens, "success": True}
        return {"idx": idx, "elapsed": round(time.time() - start, 3), "tokens": 0, "success": False}
    except Exception as e:
        elapsed = time.time() - start
        return {"idx": idx, "elapsed": round(elapsed, 3), "tokens": 0, "success": False, "error": str(e)}


def test_round_robin():
    """策略1: 轮询 — 请求依次轮流分配给各 server"""
    servers = [SERVER1, SERVER2]
    server_results = {s: [] for s in servers}
    wall_start = time.time()

    print(f"\n{'='*60}")
    print("策略1: 轮询 (round-robin)")
    print(f"{'='*60}")

    for i, prompt in enumerate(PROMPTS):
        server = servers[i % len(servers)]
        r = call_server(server, prompt, i + 1)
        server_results[server].append(r)
        status = "✅" if r["success"] else "❌"
        print(f"  [{r['idx']:2d}/30] {status} {server} | {r['elapsed']:.1f}s | {r['tokens']} tok")

    wall_time = time.time() - wall_start
    return server_results, wall_time


def test_latency_aware():
    """策略2: 按历史平均延迟分配
    - 前 2 个请求发往不同 server 做探测
    - 后续请求按历史平均延迟的倒数加权分配（快的多分）
    """
    servers = [SERVER1, SERVER2]
    server_results = {s: [] for s in servers}
    history = {s: [] for s in servers}
    wall_start = time.time()

    print(f"\n{'='*60}")
    print("策略2: 按历史平均延迟分配 (latency-aware)")
    print(f"{'='*60}")

    for i, prompt in enumerate(PROMPTS):
        if i < len(servers):
            # 前几个请求各发给一个 server 做探测
            server = servers[i]
        else:
            # 按历史平均延迟的倒数加权分配
            avg_latencies = {}
            for s in servers:
                if history[s]:
                    avg_latencies[s] = statistics.mean(history[s])
                else:
                    avg_latencies[s] = 0.001  # 极小值保底

            # 权重 = 1 / 平均延迟（延迟越低权重越大）
            weights = {s: 1.0 / avg_latencies[s] for s in servers}
            total_weight = sum(weights.values())
            probs = {s: weights[s] / total_weight for s in servers}

            # 用确定性方式按概率分配：用 idx 的 hash 做种子
            threshold = probs[servers[0]]
            if (i * 7 + 3) % 100 / 100.0 < threshold:
                server = servers[0]
            else:
                server = servers[1]

        r = call_server(server, prompt, i + 1)
        server_results[server].append(r)
        history[server].append(r["elapsed"])
        status = "✅" if r["success"] else "❌"
        print(f"  [{r['idx']:2d}/30] {status} {server} | {r['elapsed']:.1f}s | {r['tokens']} tok")

    wall_time = time.time() - wall_start
    return server_results, wall_time


def print_comparison(name, server_results, wall_time):
    """打印详细统计"""
    all_results = []
    for v in server_results.values():
        all_results.extend(v)
    all_results.sort(key=lambda x: x["idx"])

    total_reqs = len(all_results)
    total_tokens = sum(r["tokens"] for r in all_results)
    successes = sum(1 for r in all_results if r["success"])
    failed = total_reqs - successes

    latencies = sorted([r["elapsed"] for r in all_results])
    p50 = latencies[int(len(latencies) * 0.5)]
    p95 = latencies[int(len(latencies) * 0.95)]

    print(f"\n{'='*60}")
    print(f"📊 {name} 统计汇总")
    print(f"{'='*60}")
    print(f"  墙钟耗时:     {wall_time:.1f}s")
    print(f"  总请求数:     {total_reqs}")
    print(f"  成功/失败:    {successes}/{failed}")
    print(f"  总生成 token: {total_tokens}")
    print(f"  吞吐量:       {total_reqs/wall_time:.2f} req/s")
    print(f"  平均生成速度: {total_tokens/wall_time:.2f} t/s")
    print(f"  P50 延迟:     {p50:.2f}s")
    print(f"  P95 延迟:     {p95:.2f}s")

    for server, reqs in server_results.items():
        if not reqs:
            continue
        srv_latencies = [r["elapsed"] for r in reqs]
        srv_tokens = sum(r["tokens"] for r in reqs)
        srv_success = sum(1 for r in reqs if r["success"])
        print(f"\n  {server}:")
        print(f"    请求数:       {len(reqs)} (成功 {srv_success})")
        print(f"    平均延迟:     {statistics.mean(srv_latencies):.3f}s")
        print(f"    总 token:     {srv_tokens}")
        print(f"    平均速度:     {srv_tokens/max(sum(srv_latencies),0.001):.2f} t/s")


def check_servers():
    """检查两个 server 是否在线"""
    all_ok = True
    for name, url in [("server1(8080)", SERVER1), ("server2(8081)", SERVER2)]:
        try:
            resp = requests.get(f"{url}/health", timeout=5)
            if resp.status_code == 200:
                print(f"  ✅ {name} ({url}) 可用")
            else:
                print(f"  ⚠️  {name} ({url}) 响应异常: {resp.status_code}")
                all_ok = False
        except requests.ConnectionError:
            print(f"  ❌ {name} ({url}) 不可用 — 请先启动 llama-server")
            all_ok = False
    return all_ok


if __name__ == "__main__":
    print("=" * 60)
    print("负载均衡调度对比测试")
    print(f"日期: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Prompt 数: {len(PROMPTS)}")
    print(f"{'='*60}")

    # 检查 server 状态
    print("\n检查服务器状态...")
    if not check_servers():
        print("\n请先启动两个 llama-server：")
        print("  # 终端1")
        print("  cd ~/OSH-labs/llama.cpp")
        print("  ./build/bin/llama-server -m models/qwen2.5-1.5b-instruct-q4_k_m.gguf --host 0.0.0.0 --port 8080 --threads 8")
        print("  # 终端2")
        print("  ./build/bin/llama-server -m models/qwen2.5-1.5b-instruct-q4_k_m.gguf --host 0.0.0.0 --port 8081 --threads 8")
        exit(1)

    # 策略1: 轮询
    r1, t1 = test_round_robin()
    print_comparison("轮询 (round-robin)", r1, t1)

    # 策略2: 按延迟分配
    r2, t2 = test_latency_aware()
    print_comparison("按历史平均延迟分配 (latency-aware)", r2, t2)

    # 最终对比表
    print(f"\n{'='*60}")
    print("🏆 最终对比总结")
    print(f"{'='*60}")
    print(f"{'策略':<30} {'总耗时':>8} {'吞吐':>8} {'server1请求':>10} {'server2请求':>10}")
    print("-" * 66)

    for name, results, wall in [
        ("轮询 (round-robin)", r1, t1),
        ("按历史平均延迟分配", r2, t2),
    ]:
        s1_req = len(results.get(SERVER1, []))
        s2_req = len(results.get(SERVER2, []))
        total_reqs = s1_req + s2_req
        throughput = total_reqs / wall if wall > 0 else 0
        print(f"{name:<30} {wall:>8.1f}s {throughput:>8.2f} {s1_req:>10} {s2_req:>10}")

    print(f"\n💡 提示：结果已打印完毕，可复制到报告中。")
    print(f"    如需保存到文件：python3 ray_load_balance.py | tee results/load_balance_result.txt")
