# TASK-005 Agent、Memory与Trace

## 目标

支持Knowledge、Tool、Direct、Clarification、Handoff、Unknown和Refuse路由，保留最近6轮会话。

## 结果

支持“图书馆工作日几点开？它周末呢？”等指代追问。Tool缺参数时请求用户补充，不猜测学号和申请编号。每次运行保存路由、检索、Tool与观察Trace。
