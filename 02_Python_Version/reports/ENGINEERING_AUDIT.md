# Engineering Audit

| 检查项 | 初始风险 | 处理结果 |
|---|---|---|
| API Key | 密钥误提交 | `.env`已忽略，`.env.example`只保留空值 |
| 配置 | 参数散落 | `src/config.py`统一管理 |
| 日志 | 无法回放检索与Tool | 检索、RAG和Tool写入本地日志 |
| 异常 | Tool异常传到界面 | Registry捕获异常并返回结构化错误 |
| 循环 | Agent无限调用 | `MAX_AGENT_STEPS=3` |
| 输入 | 空问题、多余Tool参数 | 前端与Registry双重校验 |
| 数据 | 泄露真实个人信息 | 只使用S1001等明示模拟数据 |
| 测试 | 手工点击不可重现 | 30个`unittest`用例与一键Evaluation脚本 |

当前剩余风险：真实LLM API和生产身份认证未在当前环境实测。
