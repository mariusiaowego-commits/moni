"""moni.stats — W1 量化看板后端聚合层。

v1.0.19: moni-B §四.7 路线图 W1 申报包装,§五 评分 +2-3 分。
- repository.py: SQL 聚合 7 张表 (articles / cron_runs / llm_jobs / share_tasks / article_pins / events / cases)
- savings.py: 节省时间公式 (T₀ yaml + T₁ 实测)
- routes.py: 4 端点 (overview / timeseries / llm / savings)

数据稀疏应对 (MOA 拍板 Q6+补丁):
- 默认 N<10 触发 "数据积累中"
- 抽取层 events/cases 单独阈值 N<5 (避免两模块同时砍掉等效 Q1=B)
"""