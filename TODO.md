# TODO

## 诊断日志接入主循环

**状态**: 待办
**记录时间**: 2026-05-03

`codo/utils/diagnostics.py` 目前只接入了 prompt 构建阶段（context.py、codomd.py、attachments.py），主循环的关键事件没有记录。

需要接入的模块：
- [ ] `codo/query.py` — query_loop 每轮开始/结束、阶段切换
- [ ] `codo/query_engine.py` — API 调用耗时、submit_message_stream 生命周期
- [ ] `codo/services/tools/streaming_executor.py` — 工具注册、执行、并发批次、sibling abort
- [ ] `codo/utils/abort_controller.py` — 中断触发、回调执行
- [ ] `codo/runtime_protocol.py` — 事件发送、命令接收、权限交互
