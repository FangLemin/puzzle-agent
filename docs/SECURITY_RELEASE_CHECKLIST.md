# PuzzleOps Security Release Checklist

日期：2026-08-11

这个文件用于 GitHub 公开和上线前最后检查。目标不是让项目“看起来安全”，而是避免把真实 API Key、飞书配置、真实业务图片和本机路径一起发布出去。

## 1. 必跑命令

```bash
cd /Users/fanglemin/Desktop/puzzle-agent-python/.worktrees/multimodal-agent-runtime
python scripts/release_preflight.py
```

通过后再考虑 push 到 GitHub。

## 2. 密钥

- `.env 不提交`。
- `.env.example` 只能保留占位符。
- 不提交真实 API Key，包括：
  - `QWEN_API_KEY`
  - `DASHSCOPE_API_KEY`
  - `OPENAI_API_KEY`
  - `FEISHU_APP_SECRET`
  - `FEISHU_ACCESS_TOKEN`
  - `PUZZLEOPS_API_TOKENS`
- 文档里只能出现 `replace_me`、`your_*`、`token_jp_1` 这类示例 token。

## 3. 飞书

- 不公开真实飞书 app id、secret、spreadsheet token、bitable table id。
- README 可以写“支持飞书同步”，但不要写真实表格 URL。
- 飞书写入接口暂缓开放到 FastAPI API，当前仍走 5199 页面人工确认同步。

## 4. 数据和图片

- 真实业务图片不要直接公开，除非确认版权和脱敏。
- 如果需要公开 demo，优先使用合成示例图、脱敏截图或路径占位符。
- 真实评测集可以公开结构和统计，不一定公开原图。
- 公开前检查 `docs/eval/*.csv` 是否包含不应公开的本机绝对图片路径。

## 5. 本机路径和绝对路径

- README 可保留本机启动路径作为个人项目记录，但正式 GitHub 版建议补充相对路径启动方式。
- 公开前需要检查绝对路径，尤其是 `/Users/fanglemin/Desktop/...` 这种路径是否暴露真实业务图片、表格或客户素材。
- 文档中的 `/Users/fanglemin/...` 不能包含密钥、真实飞书 URL 或不可公开图片路径。
- `PUZZLEOPS_RUNTIME_DIR` 应指向运行环境本地目录，不进仓库。

## 6. API Token 和权限

- `PUZZLEOPS_API_TOKENS` 必须使用真实随机 token，不使用文档示例。
- 日本运营 token 只给 `日本`。
- 法国运营 token 只给 `法国`。
- `admin` token 只给负责人。
- 对外部署必须启用 HTTPS、VPN 或服务器防火墙。

## 7. 发布口径

可以写：

- 多模态 Agent Harness。
- Qwen VLM 视觉解析。
- RAG、Memory、图像相似、FastAPI、飞书落地。
- 真实小样本评测和不足分析。

不要写：

- 大规模线上稳定预测。
- 已经达到生产级预测准确率。
- 价值观大师完全自动替代运营判断。

## 8. 最后检查

- `python scripts/release_preflight.py` 通过。
- `PYTHONPATH=. pytest tests -q` 通过。
- `git status --short` 没有 `.env`。
- `git log --oneline -5` 能看到最新版本提交。
