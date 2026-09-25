# TASK-002 知识库与检索

## 目标

建立不少于10份、覆盖不少于2类主题的合规资料，实现Loader、三组Chunking、Embedding、Top-1/3/5检索和日志。

## 结果

完成12份、7类主题的模拟资料；采用512维字符n-gram Hashing Embedding和向量加词法混合检索。

## 测试

`test_ingestion_retrieval.py`检查文档数量、主题数量、不跨文档分块、边界参数、向量维度与检索来源。
