import sys, os
sys.stdout = open(r"D:\osh大作业\页面切换-基础版\output\test_out.txt", "w", encoding="utf-8")
print("hello from python")
try:
    path = r"D:\osh大作业\页面切换-基础版\output\tracepilot_events.bin"
    print(f"path exists: {os.path.exists(path)}")
    print(f"size: {os.path.getsize(path)}")
except Exception as e:
    print(f"error: {e}")
sys.stdout.close()
