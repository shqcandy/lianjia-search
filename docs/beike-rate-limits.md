# 贝壳官方接口限流核验

核验日期：2026-10-06

## 官方规则

贝壳 AI 开放平台服务协议（2026-08-21 生效）没有公布 QPS、RPM、每日调用次数或具体 429 规则，但明确禁止：

- 利用脚本、插件、模拟器、自动化程序批量调用服务接口。
- 高频请求或恶意压测服务。
- 未经书面授权系统性抓取、批量采集、复制或存储平台房源数据。
- 将平台数据用于数据库搭建、二次分发或其他非本人合法使用场景。

协议同时说明，贝壳会对接口调用进行安全监测和异常拦截，并可限制功能、暂停或终止服务。因此不存在一个可以确认“不会触发风控”的官方数值配额。

## 可观测行为

本次测试使用官方 `beike` CLI `0.2.26`，未直接调用或逆向 HTTP 接口：

- 约 8~10 次连续搜索后，CLI 返回 `service temporarily unavailable`。
- 等待 60 秒、5 分钟和 10 分钟后，单次重试仍返回相同错误。
- CLI 没有返回 `Retry-After`、余额、剩余额度或公开的 429 响应，无法据此确认精确窗口。
- GitHub 官方仓库明确说明 CLI 和 Skill 源码在私有仓库，公开仓库没有限流说明。

这些只能说明服务端存在风险控制或上游故障，不能据此反推出官方配额。

## 客户端保护

由于官方没有公布数值规则，代码采用以下保守限制：

- 两次真实请求至少间隔 60 秒。
- 每 5 分钟最多 1 次请求。
- 每小时最多 3 次请求。
- 每 24 小时最多 10 次请求。
- 已命中的本地缓存不计入请求额度。
- 连续出现 `service temporarily unavailable` 时，依次冷却 6 小时、24 小时、72 小时。
- 冷却状态保存到 `output/beike_rate_limit.json`，不会保存 API Key。

这些数字是客户端保护线，不是贝壳官方承诺，也不能保证账号绝对安全。若官方后续发布明确配额，应以官方规则为准。

## 来源

- 贝壳 AI 开放平台服务协议：https://m.ke.com/user/commonProtocol?id=beike_AI_open_Platform_private_protocol
- 贝壳 AI 开放平台仓库：https://github.com/LianjiaTech/beike-ai-platform
- 官方 CLI 说明：https://github.com/LianjiaTech/beike-ai-platform/blob/master/cli/README.md
