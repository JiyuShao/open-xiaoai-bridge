# 02: Hermes 流式连接与能力回退

**What to build:** Bridge 在支持流式的 Hermes 上发送一个 `stream=true` 请求并复用 HTTP 连接；能力不可用、返回非 SSE 或流式协议失败时，用户仍得到现有完整响应。

**Blocked by:** 01: Hermes SSE 事件合同

**Status:** ready-for-human

- [x] 每个用户轮次只发送一个请求、同一 session 和现有鉴权/会话头
- [x] 流式请求使用标准 OpenAI-compatible 参数，不修改 Hermes 镜像或代码
- [x] OpenAI manager 生命周期内复用 HTTP client，关闭时释放连接
- [x] 能力探测结果按 endpoint 缓存，失败不会反复探测或连接生产服务
- [x] 非流式 JSON、HTTP 错误、SSE 解析错误和流中断可回退完整响应
- [x] Mock HTTP server 验证请求次数、session 头、stream 标志、连接复用和回退
