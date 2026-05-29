# TracePilot eBPF Build Coordination
.PHONY: all page_switch page_turning camera clean
all: page_switch page_turning camera
	@echo "=== All scenarios built ==="
page_switch:
	$(MAKE) -C ebpf/src/????-??? bpf
page_turning:
	$(MAKE) -C ebpf/src/page_turning
camera:
	$(MAKE) -C ebpf/src/camera/ebpf
clean:
	$(MAKE) -C ebpf/src/????-??? clean || true
	$(MAKE) -C ebpf/src/page_turning clean || true
	$(MAKE) -C ebpf/src/camera/ebpf clean || true
