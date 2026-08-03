# 视觉相似人工 TopK 标注说明

## 用途

- 这是人工 TopK 标注模板，用来把 v0.7.53/v0.7.54 的 proxy eval 升级为 gold eval。
- 每一行是一张候选图和一张历史依据图的配对。
- 标注后可用于计算真实 Hit@K、MRR、NDCG、Precision@K、Recall@K 和 Bad Match Rate。

## 范围

- 国家：日本、法国
- TopK：5
- 行数：30

## 标注口径

- `manual_relevance`：相关填 1，不相关填 0，不确定填 unsure。
- `same_subject`：主体是否相同或强相关，填 1/0/unsure。
- `same_style`：风格、色彩、构图是否可参考，填 1/0/unsure。
- `usable_as_value_evidence`：是否能作为价值观大师判断依据，填 1/0/unsure。
- `human_note`：写明不相关原因，比如主体错、国家文化错、风格不一致、历史图质量差。
