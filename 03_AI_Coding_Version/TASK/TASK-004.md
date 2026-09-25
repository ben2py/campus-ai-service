# TASK-004 Tool与Function Calling

## 目标

实现业务状态查询、人工转接、Tool Schema、Registry和统一执行器。

## 结果

- `query_application_status(student_id, application_id)`
- `handoff_to_human(reason)`
- 所有Tool均返回结构化JSON，与回答生成解耦。
- Registry拒绝缺失参数、多余参数和未注册Tool。

## 测试

完成3条正常、2条非法参数、1条不存在记录和1条人工转接测试。
