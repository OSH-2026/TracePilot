#!/bin/bash
# Prompt output quality test script
# Lab 4 - OSH 2026

LLAMA_DIR="/Users/pzy/osh-llama"
MODEL="$LLAMA_DIR/models/qwen2.5-1.5b-instruct-q4_k_m.gguf"
BIN="$LLAMA_DIR/build/bin"
OUTPUT_DIR="/Users/pzy/Vscode-c/osh-2026-labs/lab4/results"
OUTPUT_FILE="$OUTPUT_DIR/prompts_output.txt"

mkdir -p "$OUTPUT_DIR"

{
prompts=(
  "请介绍一下大语言模型(LLM)的原理和应用。"
  "请用一句话总结以下内容:Transformer 架构由 Vaswani 等人在 2017 年提出，它完全基于注意力机制，摒弃了循环和卷积结构。"
  "请解释以下 Python 代码的功能：\ndef fibonacci(n):\n    a, b = 0, 1\n    for _ in range(n):\n        a, b = b, a + b\n    return a"
  "如果 3 只猫 3 分钟捉 3 只老鼠，那么 100 只猫捉 100 只老鼠需要多少分钟？"
  "在操作系统中，什么是上下文切换？它为什么会带来性能开销？"
)

for i in "${!prompts[@]}"; do
  prompt="${prompts[$i]}"
  echo ""
  echo "========================================"
  echo "Prompt $((i+1)): ${prompt:0:40}..."
  echo "========================================"
  echo "" | $BIN/llama-cli -m $MODEL \
    -p "<|im_start|>user\n${prompt}<|im_end|>\n<|im_start|>assistant" \
    -n 200 --temp 0.7 --single-turn
  echo ""
done
} 2>&1 | tee "$OUTPUT_FILE"

echo ""
echo "结果已保存到: $OUTPUT_FILE"