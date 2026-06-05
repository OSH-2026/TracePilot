#!/bin/bash
# llama.cpp Performance Benchmark Script
# Lab 4 - OSH 2026

LLAMA_DIR="/Users/pzy/osh-llama"
MODEL="$LLAMA_DIR/models/qwen2.5-1.5b-instruct-q4_k_m.gguf"
BIN="$LLAMA_DIR/build/bin"
OUTPUT_DIR="/Users/pzy/Vscode-c/osh-2026-labs/lab4/results"
OUTPUT_FILE="$OUTPUT_DIR/benchmark_results.txt"
mkdir -p "$OUTPUT_DIR"

{
echo "=== llama.cpp Performance Benchmark ==="
echo "Model: Qwen2.5-1.5B-Instruct Q4_K_M"
echo "Date: $(date)"
echo ""

echo "--- 1. Baseline (default threads) ---"
$BIN/llama-bench -m $MODEL -p 512 -n 128

echo ""
echo "--- 2. Thread Scaling ---"
$BIN/llama-bench -m $MODEL -p 512 -n 128 -t 4,8,10

echo ""
echo "--- 3. Batch Size Comparison (threads=4) ---"
$BIN/llama-bench -m $MODEL -p 512 -n 128 -t 4 -b 128,256,512

echo ""
echo "--- 4. CPU-only (ngl=0) vs GPU (default) ---"
$BIN/llama-bench -m $MODEL -p 512 -n 128 -t 4 -ngl 0

echo ""
echo "--- Done ---"
} 2>&1 | tee "$OUTPUT_FILE"

echo ""
echo "结果已保存到: $OUTPUT_FILE"