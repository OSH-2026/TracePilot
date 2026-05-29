@echo off
cd /d "D:\osh大作业\页面切换-基础版\output"
python run_analysis.py > run_output.txt 2>&1
type run_output.txt
