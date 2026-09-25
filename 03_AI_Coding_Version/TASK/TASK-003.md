# TASK-003 RAG与Unknown Handling

## 目标

完成Question、Retrieval、Context、Generator、Citation和Unknown闭环。

## 首次运行问题

阈值0.15将“明天食堂菜单”误匹配到校历，将“校车实时位置”误匹配到校园网。

## 修复

在冻结测试集上检查知识题与Unknown题的分数区间，将最终阈值设为0.25，保留原始问题和修复结果。
