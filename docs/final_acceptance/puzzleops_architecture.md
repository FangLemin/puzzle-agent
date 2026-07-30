# PuzzleOps Agent 架构说明

## 总览

```mermaid
flowchart LR
    User["运营/HITL"] --> UI["Python Server Rendered UI"]
    UI --> Agent["PuzzleOpsAgent"]
    Agent --> VLM["Qwen VLM\n主体/色彩/构图"]
    Agent --> RAG["Value/Audit RAG"]
    Agent --> Memory["四层 Memory\n感知/短期/长期/事实"]
    Agent --> History["真实历史样本\n日本/法国"]
    Agent --> Generator["ImageGenerationProvider\nDashScope/ComfyUI/Mock"]
    Agent --> Feishu["Feishu Client\n多维表格/附件"]
    Agent --> Harness["Agent Harness\nTrace/Eval/HITL"]
    RAG --> Store["SQLite + Milvus/Zilliz\nChunk/Vector/Metadata"]
    Harness --> Reports["Eval Reports\nREADME/简历证据"]
```

## Agent 主链路

```mermaid
sequenceDiagram
    participant O as 运营
    participant UI as 试新/价值观页面
    participant A as PuzzleOpsAgent
    participant V as Qwen VLM
    participant R as RAG Retriever
    participant M as Memory
    participant F as Feishu

    O->>UI: 上传参考图/候选图
    UI->>A: 创建试新提需
    A->>V: 解析主体/色彩/构图/风险
    A->>R: 召回国家价值观和审核规则
    R->>M: 读取 approved memory/facts
    R-->>A: 返回 citation + rerank trace
    A-->>UI: 输出价值观判断/风险/提需字段
    O->>UI: 编辑主体描述/确认
    UI->>A: 同步飞书
    A->>F: 写入字段与附件
```

## RAG 离线阶段

```mermaid
flowchart TD
    Docs["价值观规则/审核规则/Gold样本/Memory"] --> Loader["Document Loader"]
    Loader --> Splitter["语义边界 Chunk\nchunk size + overlap"]
    Splitter --> ParentChild["父子文档元数据"]
    ParentChild --> SQLite["SQLite Chunk Store"]
    ParentChild --> Embedding["Embedding Provider\nlocal 或 DashScope"]
    Embedding --> VectorDB["Milvus/Zilliz/Qdrant"]
    SQLite --> EvalSet["RAG Eval Dataset"]
```

## RAG 在线阶段

```mermaid
flowchart TD
    Query["国家+主体+场景+运营tag+风险词"] --> Rewrite["Query 构造/轻改写"]
    Rewrite --> BM25["BM25 关键词召回"]
    Rewrite --> Vector["向量召回"]
    BM25 --> Pool["候选 Chunk Pool"]
    Vector --> Pool
    Pool --> Rerank["Qwen Rerank 或 Local Rerank"]
    Rerank --> Filter["强 citation 过滤\nTop3 + hard-negative block"]
    Filter --> Prompt["拼接 Prompt\n问题+图像证据+citation"]
    Prompt --> LLM["LLM 生成价值观判断"]
    LLM --> Trace["保存 citation/trace/stats"]
```

## Memory 四层

```mermaid
flowchart TD
    Perception["感知记忆\nVLM观察/图片解析"] --> Working["短期记忆\n任务状态/生成trace"]
    Working --> Facts["结构化事实\n主体/tag/gold/指标"]
    Facts --> LongTerm["长期记忆\n人工确认价值观"]
    LongTerm --> Gate["RAG准入闸门\napproved + active + no conflict"]
    Facts --> Gate
    Gate --> RAG["RAG Knowledge Base"]
    Human["运营审核"] --> Gate
    RAG --> Hit["命中回写\nhit_count/last_hit_at"]
```

## Harness 评测闭环

```mermaid
flowchart LR
    Dataset["真实小样本\n45/50"] --> Run["HarnessRun"]
    Run --> Cases["HarnessCaseResult"]
    Cases --> Metrics["指标\nHit/MRR/NDCG/字段完整/工具正确"]
    Cases --> Failures["失败分类\nRAG噪声/历史依据/指标校准"]
    Failures --> HITL["人工修正"]
    HITL --> Memory["Memory/Facts"]
    Metrics --> Reports["报告导出"]
    Reports --> Resume["简历级证据"]
```

## 好图衍生链路

```mermaid
flowchart TD
    Ref["上传历史好图"] --> Parse["Qwen VLM 解析"]
    Parse --> Prompt["生成衍生 Prompt"]
    Prompt --> Gen["DashScope/通义万相"]
    Gen --> NewImage["生成参考图"]
    NewImage --> Reparse["二次 VLM 解析"]
    Reparse --> Audit["审核规则复检"]
    Audit --> Human["运营人工确认"]
    Human --> Trial["进入试新提需表"]
    Trial --> Feishu["飞书附件同步"]
```

## 当前边界

- 主等级预测仍保留 `v0.7.39-legacy`，不因第三层 shadow 实验自动替换。
- RAG citation 已能召回 expected，但 TopK 仍存在 hard-negative 噪声。
- 历史依据 shadow rerank 只作为影子能力，不默认上线。
- 当前 45 条真实样本适合简历 demo 和小样本评测，不适合宣称大规模线上效果。

