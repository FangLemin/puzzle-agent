# 版本记录

这个文件用来记录每一版做了什么、为什么改、当前还存在哪些问题。以后每次你让我修改功能，我会先提交旧版本，再在这里追加阶段总结。

## v0.3.27 - 修复同步成功后飞书打开 404

日期：2026-06-09

阶段目标：

- 修复点击同步成功提示里的飞书入口后，飞书显示“页面不存在”的问题。

根因：

- `FEISHU_SPREADSHEET_TOKEN` 当前是云文档节点 token。
- `web_url()` 仍用这个 token 拼 `https://feishu.cn/base/{token}?table=...`。
- 飞书网页端需要真实 bitable app token，所以打开后进入 404/页面不存在。

已完成：

- `RealFeishuClient.web_url()` 在 bitable 场景使用 canonical app token：
  - 如果已经配置或缓存 `FEISHU_BITABLE_APP_TOKEN`，直接使用。
  - 如果没有，则通过飞书接口解析真实 app token 后生成链接。
  - 同步成功后的“打开飞书表格”按钮会指向真实 base app token URL。
- 保留 `FEISHU_WEB_URL` 的最高优先级：
  - 如果你手动配置了完整飞书网页链接，仍以手动配置为准。

验证记录：

- `PYTHONPATH=. pytest tests/test_external_adapters.py::test_real_feishu_client_bitable_web_url_uses_configured_canonical_app_token tests/test_external_adapters.py::test_real_feishu_client_bitable_web_url_resolves_canonical_app_token_when_needed -q`：2 passed。
- `PYTHONPATH=. pytest tests/test_external_adapters.py -q`：21 passed。
- `PYTHONPATH=. pytest tests -q`：124 passed。
- 真实配置下 `RealFeishuClient.web_url()` 生成 `https://feishu.cn/base/AgqW...?...`，不再使用 `CxCTw...` 云文档节点 token。

## v0.3.26 - 按真实飞书字段动态过滤同步 payload

日期：2026-06-09

阶段目标：

- 修复真实飞书表缺少 `价值观匹配度` 字段时，同步报 `FieldNameNotFound` 的问题。

根因：

- 本地 bitable 白名单允许写入 `价值观匹配度`。
- 但用户当前真实飞书提需表没有这个字段，飞书 batch_create 会直接拒绝整个请求。

已完成：

- 多维表格同步前读取真实表字段：
  - 调用 `/bitable/v1/apps/{app_token}/tables/{table_id}/fields?page_size=200`。
  - 有远端字段列表时，只写当前飞书表真实存在的字段。
  - 如果字段列表取不到或为空，才回退到本地白名单，避免弱权限场景完全不可用。
- `价值观匹配度` 变为可选同步字段：
  - 飞书表有这个字段就写。
  - 飞书表没有这个字段就自动跳过，不影响图片、运营 tag、主体描述等核心字段同步。

验证记录：

- `PYTHONPATH=. pytest tests/test_external_adapters.py::test_real_feishu_client_omits_bitable_fields_missing_from_remote_schema -q`：1 passed。
- `PYTHONPATH=. pytest tests/test_external_adapters.py -q`：19 passed。
- `PYTHONPATH=. pytest tests -q`：122 passed。

## v0.3.25 - 修复飞书附件上传异常导致本地页面断连

日期：2026-06-09

阶段目标：

- 修复点击“同步试新到飞书”后 Safari 停在 `/sync_trial_feishu` 并提示本地服务器中断连接的问题。

根因：

- 真实飞书附件上传接口返回 `parent node not exist`。
- 当前代码没有捕获附件上传阶段的 `RuntimeError`，导致本地 HTTP 请求处理线程异常退出，浏览器只能看到“服务器意外中断了连接”。
- 当前 `.env` 里的 `FEISHU_SPREADSHEET_TOKEN` 是云文档节点 token 形态，附件上传需要真实 bitable app token 作为 `parent_node`。

已完成：

- `RealFeishuClient.write_table()` 捕获真实飞书 HTTP/素材上传异常：
  - 失败时返回 `ToolResult(success=False)`。
  - 页面会显示“同步失败：...”的具体飞书错误，不再让浏览器断连。
- 多维表格附件上传改用 canonical bitable app token：
  - 优先读取可选配置 `FEISHU_BITABLE_APP_TOKEN`。
  - 如果未配置，则通过飞书 bitable app 查询接口自动把当前 token 解析为真实 app token。
  - `upload_all` 的 `parent_node` 使用解析后的 app token。
  - 写入记录的 batch_create URL 也使用解析后的 app token。
- `.env.example` 增加 `FEISHU_BITABLE_APP_TOKEN` 可选项说明。

验证记录：

- `PYTHONPATH=. pytest tests/test_external_adapters.py::test_real_feishu_client_upload_uses_canonical_bitable_app_token tests/test_external_adapters.py::test_real_feishu_client_returns_failure_when_bitable_attachment_upload_fails -q`：2 passed。
- `PYTHONPATH=. pytest tests/test_external_adapters.py tests/test_server.py::test_sync_trial_to_feishu_records_success_and_resets_trial_row tests/test_server.py::test_sync_needs_to_feishu_clears_rows_and_sets_success_message -q`：20 passed。
- `PYTHONPATH=. pytest tests -q`：121 passed。

## v0.3.24 - 同步确认页与单页提需卡片

日期：2026-06-09

阶段目标：

- 修复点击同步后飞书页面无法稳定打开的问题，并把常规/试新提需表改成更适合运营审核的单页编辑布局。

已完成：

- 同步成功后不再把 POST 请求直接 303 到飞书外链：
  - 服务端先回到当前 Agent 页面，展示同步成功状态。
  - 成功提示里提供“已同步，打开飞书表格”按钮，保留 `target="_blank"` 让运营主动打开飞书。
  - 即使浏览器拦截弹窗或外链跳转，页面也会明确显示同步结果和飞书入口。
- 常规提需和试新提需从超宽表格改为卡片式行编辑：
  - 图片、运营 tag、主体内容、张数、需求等级、加工方式、交付日期放在紧凑网格里。
  - 主体描述、备注、价值观匹配度放到下方宽区域，避免在窄列里夹缝审核 AI 文案。
  - 同步按钮不再依赖 `formtarget="_blank"`，减少浏览器弹窗策略影响。

验证记录：

- `PYTHONPATH=. pytest tests/test_server.py::test_sync_needs_to_feishu_clears_rows_and_sets_success_message tests/test_server.py::test_sync_trial_to_feishu_records_success_and_resets_trial_row tests/test_renderer.py::test_regular_page_renders_business_table_fields_and_empty_delivery_input tests/test_renderer.py::test_trial_page_keeps_core_fields_and_value_match_column tests/test_renderer.py::test_sync_success_message_renders_feishu_link_without_popup_dependency -q`：5 passed。
- `PYTHONPATH=. pytest tests -q`：119 passed。

## v0.3.23 - 同步跳转稳定性与提需表列宽优化

日期：2026-06-09

阶段目标：

- 修复同步成功后新页面打不开的问题，并优化试新/常规提需表列宽，避免主体描述在窄列中难以审核。

已完成：

- 同步跳转不再额外调用飞书 API：
  - `RealFeishuClient.web_url()` 对 bitable 直接返回 `https://feishu.cn/base/{app_token}?table={table_id}`。
  - 避免同步成功后为了获取 canonical app token 又发一次 GET，导致新窗口打不开或卡住。
  - 如果配置了 `FEISHU_WEB_URL` 但没有 `https://`，会自动补齐协议。
- 提需表增加固定列宽：
  - 新增 `demand-table`、`regular-demand-table`、`trial-demand-table` 和 `colgroup`。
  - 张数列压到 72px，需求等级 118px，加工方式 150px，交付日期 92px。
  - 主体描述列常规 520px，试新 620px，价值观匹配度 760px。
  - 主体描述 textarea 高度提升到 220px，便于运营审核和改写 AI 文案。

验证记录：

- `PYTHONPATH=. pytest tests/test_external_adapters.py::test_real_feishu_client_bitable_web_url_does_not_require_extra_api_call tests/test_external_adapters.py::test_real_feishu_client_normalizes_configured_web_url tests/test_renderer.py::test_regular_page_renders_business_table_fields_and_empty_delivery_input tests/test_renderer.py::test_trial_page_keeps_core_fields_and_value_match_column -q`：4 passed。
- `PYTHONPATH=. pytest tests -q`：118 passed。
- 浏览器验证：试新表 `col-count=72px`、`col-priority=118px`、主体描述 textarea `min-height=220px`、同步按钮 `formtarget=_blank`。

## v0.3.22 - 试新上传图片自动同步为飞书附件

日期：2026-06-09

阶段目标：

- 打通“Agent 试新模块上传一次图片 -> 飞书素材上传 -> 获取 file_token -> 写入多维表格附件字段”的完整链路。

已完成：

- 试新上传解析后保留本地图片路径：
  - `DemandRow` 新增 `reference_image_path` 和 `reference_image_content_type`。
  - 上传图片保存到本地后，提需行会携带 URL、path、content-type。
- `RealFeishuClient` 新增 `upload_bitable_attachment`：
  - 调用飞书 `POST /open-apis/drive/v1/medias/upload_all`。
  - `parent_type` 根据文件类型选择 `bitable_image` 或 `bitable_file`。
  - `parent_node` 使用多维表格 app token。
  - 成功后读取 `data.file_token`。
- 真实 bitable 同步前自动上传附件：
  - 如果提需 payload 带 `_reference_image_path`，且 `图片本身` 还不是 `file_token` 附件格式，会先上传素材。
  - 上传成功后将 `图片本身` 改写为 `[{file_token: "..."}]`。
  - `_reference_image_path`、`_reference_image_content_type` 等内部字段不会写入飞书表。
- 真实飞书多维表格里 `图片本身` 可以继续保持附件字段：
  - 不需要新增 `图片链接` 字段。
  - 不需要把 `图片本身` 改成文本字段。

当前限制：

- 飞书素材上传接口限制单文件不超过 20 MB；更大的文件需要后续接分片上传。
- 应用需要具备多维表格编辑与上传图片/附件到云文档相关权限，否则飞书会返回权限错误。

验证记录：

- `PYTHONPATH=. pytest tests/test_external_adapters.py::test_real_feishu_client_uploads_bitable_image_and_returns_file_token tests/test_external_adapters.py::test_real_feishu_client_uploads_local_image_before_bitable_create tests/test_server.py::test_trial_upload_uses_real_semantic_subject_in_operation_tag_and_feishu_payload -q`：3 passed。
- `PYTHONPATH=. pytest tests -q`：117 passed。

## v0.3.21 - 飞书字段白名单、短 tag 主体与主体描述编辑

日期：2026-06-09

阶段目标：

- 修复真实飞书表不存在 `图片链接` 字段导致同步失败、AI 生成运营 tag 主体过长、常规/试新提需表主体描述不可编辑的问题。

已完成：

- 飞书 bitable 同步增加字段白名单：
  - 只写入当前提需表已有字段：提需分类、国家、JS分类、图片本身、运营tag、主体内容、张数、需求等级、加工方式、交付日期、主体描述、备注、价值观匹配度。
  - `图片链接`、`不存在字段` 等未建字段不再写入真实多维表格，避免 `FieldNameNotFound`。
  - `图片本身` 仍只在有真实附件 `file_token` 时写入。
- 试新运营 tag 主体压缩：
  - 不再把 VLM 的完整长句直接写入 tag。
  - 长主体会抽取 8 字以内运营短主体，例如 `游客群体含儿童与背包行人在观景步道上行走背景为传统日式多层塔楼建筑` 压缩为 `游客塔楼`。
  - 保留常见业务短主体，如寿司、抹茶、传统浴袍美女、3D渲染动物拟人化等。
- 常规提需表和试新提需表的 `主体描述` 改为可编辑：
  - 页面渲染为 textarea。
  - 保存接口会保存运营人工改写后的主体描述。

当前限制：

- 如果需要把上传图片真正内嵌到飞书附件字段，仍需新增飞书文件上传流程并拿到 `file_token`。
- tag 主体压缩目前是运营短词抽取规则，后续可让 LLM 单独输出 `operation_tag_subject` 字段，并限制 8 字以内。

验证记录：

- `PYTHONPATH=. pytest tests/test_external_adapters.py::test_real_feishu_client_omits_link_style_image_field_for_bitable_attachment tests/test_external_adapters.py::test_real_feishu_client_omits_unknown_bitable_fields_to_match_existing_schema tests/test_server.py::test_trial_upload_compacts_long_semantic_subject_for_operation_tag tests/test_server.py::test_save_trial_can_edit_subject_description tests/test_renderer.py::test_regular_page_renders_business_table_fields_and_empty_delivery_input tests/test_renderer.py::test_trial_page_keeps_core_fields_and_value_match_column -q`：6 passed。
- `PYTHONPATH=. pytest tests -q`：115 passed。

## v0.3.20 - 修复飞书多维表格图片附件字段同步失败

日期：2026-06-09

阶段目标：

- 修复真实飞书多维表格同步报错 `AttachFieldConvFail`，原因是把普通图片链接对象写入了附件字段 `图片本身`。

已完成：

- 修复 bitable 字段转换：
  - `图片本身` 只有在值为真正附件格式 `[{file_token: "..."}]` 时才写入。
  - 普通文本、普通链接对象 `[{text, link}]` 不再写入 `图片本身`，避免飞书附件字段转换失败。
  - `图片链接` 字段继续保留上传图 URL，用于同步后追溯参考图。
- 保留未来扩展空间：
  - 后续若接入飞书文件上传拿到 `file_token`，`图片本身` 附件字段会自动保留并写入。

当前限制：

- 当前还没有实现飞书附件上传，所以真实多维表格里不会把图片作为附件内嵌到 `图片本身` 字段；本版先保证同步成功，并把图片 URL 写入 `图片链接` 字段。

验证记录：

- `PYTHONPATH=. pytest tests/test_external_adapters.py::test_real_feishu_client_omits_plain_text_attachment_fields_for_bitable tests/test_external_adapters.py::test_real_feishu_client_omits_link_style_image_field_for_bitable_attachment tests/test_external_adapters.py::test_real_feishu_client_keeps_real_attachment_file_tokens_for_bitable tests/test_server.py::test_trial_upload_uses_real_semantic_subject_in_operation_tag_and_feishu_payload -q`：4 passed。
- `PYTHONPATH=. pytest tests -q`：112 passed。

## v0.3.19 - 价值观大师接入真实 LLM 判断链路

日期：2026-06-09

阶段目标：

- 修正 v0.3.18 将价值观大师做成文本规则分支的问题，改为通过 LLM 基于当前图片解析结果和已有价值观规则做判断。

已完成：

- `OpenAIVisionLLMClient` 和 `QwenVisionLLMClient` 新增 `judge_value_match`：
  - 输入当前提需行的国家、JS分类、运营 tag、主体、主体描述、解析备注。
  - 输入当前国家已有价值观规则库。
  - Prompt 明确要求不要套默认模板，必须引用当前主体、色彩氛围、构图环境证据。
  - 输出 JSON：`value_match`、`confidence`、`evidence`、`risk_tags`。
- `apply_value_master` 改为调用真实 LLM 判断：
  - 有 Qwen/OpenAI 配置时返回真实 LLM 的价值观判断。
  - 缺少真实 LLM 配置时只提示需要配置，不再伪造“符合/不符合”结论。
- 删除 v0.3.18 的手写主体规则分支：
  - 寿司、火车少女、猫咪等不再靠 if/else 判断。
  - 价值观结论由模型读取当前图片解析结果和规则库后生成。

当前限制：

- 当前价值观大师复用视觉 LLM provider 做文本判断，仍以试新上传时保存的 VLM 解析结果作为主要视觉证据；如果要在价值观按钮点击时重新读原图做二次视觉判断，可以继续把 `reference_image_url` 解析成图片 bytes 后传入同一次多模态请求。
- 模型判断仍需运营审核，尤其是版权/IP、文化混淆和品牌露出风险。

验证记录：

- `PYTHONPATH=. pytest tests/test_vision_llm.py::test_openai_client_judges_value_match_with_current_visual_context tests/test_vision_llm.py::test_qwen_client_judges_value_match_with_chat_completions_payload tests/test_agents.py::test_value_master_writes_value_match_to_trial_row tests/test_agents.py::test_value_master_uses_current_trial_subject_instead_of_default_template tests/test_agents.py::test_value_master_requires_real_llm_instead_of_rule_fallback -q`：5 passed。
- `PYTHONPATH=. pytest tests -q`：110 passed。

## v0.3.18 - 价值观大师改为基于当前解析主体判断

日期：2026-06-09

阶段目标：

- 修复试新上传寿司图后，价值观大师仍套用日本默认“猫咪鲤鱼/动物互动”文案的问题。

已完成：

- `apply_value_master` 不再直接读取国家默认 trial 模板。
- 价值观大师会读取当前提需行的：
  - `subject`
  - `operation_tag`
  - `subject_description`
- 日本市场按主体类型生成匹配理由：
  - 寿司、抹茶、料理等走“本土饮食文化、清爽色彩、生活烟火气”。
  - 猫、犬、鲤鱼等动物主体才走“治愈、季节感、动物互动”。
  - 火车、站台、店铺、少女、人物等走“日常故事感、街景氛围、主体清晰”。
- 法国市场保留花艺/庭院/餐饮等主体分支，不再只套固定文案。

当前限制：

- 价值观大师当前仍是规则化业务判断，不调用额外 LLM；好处是稳定、便宜、可控，后续可以把真实 VLM 解析结果和 RAG 价值观规则一起交给 LLM 做更自然的解释。

验证记录：

- `PYTHONPATH=. pytest tests/test_agents.py::test_value_master_writes_value_match_to_trial_row tests/test_agents.py::test_value_master_uses_current_trial_subject_instead_of_default_template tests/test_server.py::test_apply_value_master_action_updates_trial_row -q`：3 passed。
- `PYTHONPATH=. pytest tests -q`：106 passed。

## v0.3.17 - 试新 tag、上传图片同步与图片数据可信度修复

日期：2026-06-09

阶段目标：

- 修复试新提需表里运营 tag 拥挤、tag 未跟随视觉解析主体更新、上传图片未进入提需表和飞书 payload、合成数据仍是 1x1 占位图的问题。

已完成：

- 试新上传解析后会用视觉模型返回的主体重写运营 tag：
  - 例如真实视觉模型识别为 `日式火车店铺少女` 时，tag 写为 `试新_日本_日式火车店铺少女0609`。
  - 日期使用当天日期后缀，不再保留旧的 `0604`。
- 试新提需表新增上传图贯通：
  - `DemandRow` 增加 `reference_image_url`。
  - 上传解析后保存第一张上传图片的 `/uploads/...` URL。
  - 提需表“图片本身”列优先展示真实上传图，不再只根据图片名生成示意图。
- 飞书同步 payload 增加图片信息：
  - `图片本身` 在有上传图时写为带 `text/link` 的结构化链接。
  - 额外写入 `图片链接` 字段，便于飞书表格里追溯上传参考图。
- 提需表运营 tag 输入框加宽：
  - 增加 `operation-tag-input` 样式，长 tag 不再挤成看不见字。
- 合成历史数据不再写 1x1 透明占位图：
  - `SyntheticDataGenerator` 改为生成 360x240 本地拼图风格 PNG。
  - 这仍是本地演示数据，不等同真实生产图库。

当前限制：

- 真实飞书多维表格若“图片本身”字段是附件类型，仍需要后续接飞书附件上传/file_token 才能变成真正内嵌附件；当前先同步可点击图片链接和结构化链接。
- 静态库存和合成历史数据已经不是文字卡/1x1 占位图，但仍是本地生成演示图。要完全解决“真实拼图图片数据集”，需要接真实 CMS/素材库图片 URL 或导入带真实图片的业务 Excel。
- 视觉主体以真实 VLM 返回为准，但仍建议运营审核后再同步飞书。

验证记录：

- `PYTHONPATH=. pytest tests/test_server.py::test_upload_trial_images_writes_real_openai_semantics_when_configured tests/test_server.py::test_trial_upload_uses_real_semantic_subject_in_operation_tag_and_feishu_payload tests/test_renderer.py::test_trial_page_keeps_core_fields_and_value_match_column tests/test_renderer.py::test_trial_need_table_renders_uploaded_image_url_when_available tests/test_synthetic_runtime_tools.py::test_synthetic_generator_creates_139_rows_per_country_week_with_images -q`：5 passed。
- `PYTHONPATH=. pytest tests -q`：105 passed。

## v0.3.16 - 多模态业务闭环与页面可信度修正

日期：2026-06-09

阶段目标：

- 把真实视觉模型能力接到更贴近业务的提需与展示链路，修正常规提需日期、试新解析标准、库存图片展示、数据分析明细和 Agent 测评逻辑。

已完成：

- 常规提需加入时会把运营 tag 尾部日期替换为当天日期：
  - 例如 `常规_日本_传统浴袍美女0604` 在 2026-06-09 加入提需后写为 `常规_日本_传统浴袍美女0609`。
  - 手动编辑保存仍保留运营自己输入的 tag。
- 常规提需“AI生成描述”改为业务三段式：
  - 只输出 `主体内容`、`色彩氛围`、`构图环境`。
  - 服务端启用真实视觉模型通道；缺模型或调用失败时保留本地视觉特征和人工确认提示，不伪造真实主体识别。
- 试新上传解析统一为三段式业务文案：
  - 图片主体、色彩氛围、构图环境进入 `subject_description`。
  - 视觉模型 provider、置信度、风险和未配置提示留在备注。
  - “好图衍生提需”明确只输出衍生方向，不声称生成新参考图。
- 库存、价值观、数据分析明细和多模态底座不再使用文字卡：
  - 新增本地 PNG 视觉资产层，页面以真实 `<img>` 渲染参考图和明细图片。
  - 数据分析图片明细第一列展示图片预览和图片名，便于复盘色彩、构图、来源和位置差异。
- Agent 测评页按工作流重构：
  - 拆为任务目标、输入与上下文、工具调用链路、指标与结论。
  - 保留 Eval Dataset、Case 明细、Pass/Fail、工具正确性和 TruLens 指标，展示逻辑更适合讲 Agent 工作流闭环。

当前限制：

- 库存参考图目前是本地生成的拼图风格 PNG，用于替代文字卡和支撑页面多模态展示；若要完全等同真实生产图库，还需要接入真实素材库/CMS 图片 URL。
- 常规提需的视觉模型输出仍需要人工审核，版权/IP、主体识别和文化元素不能自动放行。
- 试新衍生模式仍不会生成新图，只提供衍生方向和提需文案。

验证记录：

- `PYTHONPATH=. pytest tests -q`：103 passed。

## v0.3.15 - 强制真实视觉 LLM 配置

日期：2026-06-08

阶段目标：

- 按用户要求取消视觉语义解析的 Mock 运行路径，试新图片语义解析必须接真实视觉模型。

已完成：

- `VisionLLMClientFactory` 改为强制真实视觉 LLM：
  - 默认 provider 为 `qwen`。
  - 默认模型为 `qwen3-vl-flash`，走 Qwen Cloud OpenAI-compatible Chat Completions。
  - 缺少 `QWEN_API_KEY` 时不再回退 Mock。
  - 仍保留 OpenAI 作为可选真实 provider，但不作为默认方案。
  - 页面和上传结果会明确提示“需要配置真实视觉 LLM”，当前不会做语义解析。
- 删除视觉 LLM Mock client 的运行路径，保留本地视觉解析作为低成本像素层：
  - 本地解析仍可输出颜色、构图、明暗、质量和拼图友好度。
  - 语义主体、场景、文化元素、风格和风险必须由真实视觉模型返回。
- 试新上传链路新增真实模型单元验证：
  - 使用 fake transport 验证 Qwen Chat Completions payload、OpenAI Responses API payload 和结构化结果融合。
  - 缺真实配置时，提需字段会写入“待真实视觉 LLM 解析”，不会伪造主体。
- `.env.example` 改为真实模型配置模板：
  - `VISION_LLM_PROVIDER=qwen`
  - `QWEN_API_KEY=`
  - `QWEN_VISION_MODEL=qwen3-vl-flash`
  - `QWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions`

当前限制：

- 当前已通过真实 Qwen3-VL-Flash API 验证直连解析；后续可继续补更贴近业务真实图的回归样例。
- 真实模型输出仍需人工审核，版权/IP、文化元素和主体判断不能完全自动放行。

验证记录：

- `PYTHONPATH=. pytest tests/test_vision_llm.py tests/test_renderer.py tests/test_server.py -q`：40 passed。
- `PYTHONPATH=. pytest tests -q`：101 passed。
- 真实 Qwen3-VL-Flash 调用验证通过：返回 `provider=qwen`、主体、场景、文化元素、风险标签和置信度。
- 页面上传链路端到端验证通过：`/upload_trial_images` 写入 `视觉LLM：真实qwen`、语义主体、场景、文化元素、语义风险和置信度。

## v0.3.14 - 视觉 LLM 适配层与语义解析 Mock

日期：2026-06-08

阶段目标：

- 在不破坏本地 demo 和现有飞书链路的前提下，搭建真正多模态语义解析的工程入口：默认 Mock，可选接入 OpenAI 视觉 LLM。

已完成：

- 新增 `VisionLLMClient` 适配层：
  - 默认使用 `MockVisionLLMClient`，无需网络、无需密钥、无 API 成本。
  - 可通过 `VISION_LLM_PROVIDER=openai` + `OPENAI_API_KEY` 启用真实 OpenAI 视觉解析。
  - OpenAI 适配器使用 Responses API 的 `input_text + input_image(data URL)` 形态，支持 `OPENAI_VISION_MODEL` 和 `OPENAI_VISION_DETAIL` 配置。
- 试新上传链路升级为“双层解析”：
  - 本地视觉解析负责尺寸、色彩、构图、明暗、质量和拼图友好度。
  - 视觉 LLM 适配层负责主体、场景、文化元素、风格、语义风险和 prompt 关键词。
  - 默认 Mock 会明确标注“不代表真实主体识别”；真实 OpenAI 模式才会调用外部视觉模型。
- 页面增加模型状态说明：
  - 试新提需页展示“视觉 LLM 语义解析”当前模式。
  - 多模态底座展示“视觉 LLM 适配器”状态，便于面试演示工程边界。
- `.env.example` 增加可选视觉 LLM 配置，不新增必填项，不提交真实密钥。

当前限制：

- Mock 模式仍然不是图片真实语义理解，只是为了稳定演示 Agent 工程链路。
- 真实 OpenAI 模式需要用户在本地 `.env` 配置 `OPENAI_API_KEY`，且会产生网络调用和 API 成本。
- 真实模型输出仍需人工审核，版权/IP、文化混淆和主体判断不能完全自动放行。

验证记录：

- 新增视觉 LLM Mock、OpenAI payload 构造、试新语义融合、页面模型状态测试。
- `PYTHONPATH=. pytest tests/test_vision_llm.py tests/test_server.py -q`：24 passed。
- `PYTHONPATH=. pytest tests/test_renderer.py tests/test_vision_llm.py tests/test_server.py -q`：38 passed。
- `PYTHONPATH=. pytest tests -q`：99 passed。

## v0.3.13 - 多模态本地解析与分析增强

日期：2026-06-08

阶段目标：

- 在不接真实视觉 LLM 的前提下，把试新上传图片解析升级为可复用的本地多模态特征层，并接入多模态底座、价值观大师和数据分析展示。

已完成：

- 新增 `LocalImageAnalyzer` 本地视觉解析器：
  - 支持多主色/调色板摘要。
  - 识别明暗、饱和度、冷暖色倾向。
  - 判断横向/竖向/方形构图。
  - 标记过暗、过亮、低对比/纯色等本地质量风险。
  - 输出拼图友好度建议，提示主体边界、材质纹理和前中后景层次。
- 试新上传解析改为复用本地视觉解析器：
  - `parse` 模式支持多张参考图的共同视觉特征汇总。
  - `derive` 模式输出衍生方向，不再声称真实生成新图。
  - 解析结果继续写入现有试新提需表字段，不改变飞书表结构。
- 多模态底座增强：
  - 有本地历史图片时优先使用真实像素特征。
  - 无本地图片时保留运营 tag/source 规则 fallback。
  - 页面展示明暗、饱和度、冷暖、质量标签和拼图友好度。
- 多模态分析增强：
  - 数据分析大师增加视觉维度复盘。
  - 价值观候选理由增加视觉证据，用于面试展示 Agent 的图文融合归因链路。

当前限制：

- 当前仍未接入视觉 LLM，不能真正识别图片主体、IP、版权来源或复杂语义。主体仍依赖文件名、运营 tag 或默认配置；版权/IP 审核仍以文本规则和审核手册召回为主。

验证记录：

- 新增本地视觉解析、试新多图汇总、好图衍生方向、多模态特征优先级、价值观视觉证据测试。
- `PYTHONPATH=. pytest tests/test_visual_analysis.py tests/test_multimodal_core.py tests/test_server.py -q`：31 passed。
- `PYTHONPATH=. pytest tests/test_agents.py tests/test_renderer.py tests/test_server.py tests/test_multimodal_core.py -q`：52 passed。
- `PYTHONPATH=. pytest tests -q`：94 passed。

## v0.3.12 - 试新图片真实本地视觉解析

日期：2026-06-08

阶段目标：

- 修复试新模块“上传图片后没有真正解析图片”的核心问题。

已完成：

- 定位根因：此前试新解析只保存文件并按文件名猜主体，色彩和构图均为固定文案，不是真正视觉解析。
- 新增基于 Pillow 的本地图片解析能力：
  - 读取图片像素。
  - 提取图片尺寸。
  - 判断横向/竖向/方形构图。
  - 提取平均主色并转成业务可读色彩描述。
  - 计算整体亮度。
- 上传成功后，解析结果会写入试新提需表的主体描述和备注字段，并新增试新提需记录。

当前限制：

- 本地解析可识别主色、尺寸、明暗和构图，但不能真正识别“图里是什么主体”。主体识别仍依赖文件名/国家试新默认配置，后续需要接入视觉 LLM 或图像分类模型。

验证记录：

- 上传一张 120x60 暖红色测试图后，试新表写入“暖红”“横向构图”“120x60”。
- `PYTHONPATH=. pytest tests -q`：86 passed。

## v0.3.11 - 空提需同步拦截

日期：2026-06-08

阶段目标：

- 修复点击同步时飞书返回 `records can not be empty` 的问题。

已完成：

- 常规提需清单为空时，不再请求飞书，页面提示“请先加入至少一条常规提需，再同步飞书表格。”
- 试新提需未上传解析或模拟生成记录时，不再请求飞书，页面提示“请先上传解析图片或模拟上传，生成至少一条试新提需记录。”
- 避免空 `records` 触发飞书 `WrongRequestBody`。

验证记录：

- 新增空常规提需同步测试。
- 新增空试新提需同步测试。
- `PYTHONPATH=. pytest tests -q`：85 passed。

## v0.3.10 - 飞书网页跳转真实 app_token 修复

日期：2026-06-08

阶段目标：

- 修复同步成功后新窗口打开飞书时显示“页面不存在”的问题。

已完成：

- 定位根因：用户配置的 token 可用于开放平台 API 写入，但不是网页端最终打开 Base 页面所需的 canonical app_token。
- `RealFeishuClient.web_url()` 现在会调用飞书 Base 元数据接口解析真实 `app_token`，再生成网页跳转地址。
- 保留 `FEISHU_WEB_URL` 手动覆盖能力；如果后续飞书租户域名特殊，可以直接配置浏览器地址栏里的完整 URL。

验证记录：

- 单测覆盖：`RealFeishuClient.web_url()` 会从 metadata 解析 canonical app_token。
- 真实配置解析出的跳转地址已从原 token 切换为飞书返回的 canonical app_token。

## v0.3.9 - 同步新窗口与试新交互稳定性修复

日期：2026-06-08

阶段目标：

- 修复常规提需同步后当前运营后台被飞书页面替换的问题，并加固常规/试新表单上下文，避免旧页面状态导致试新上传或提需动作串到错误模块。

已完成：

- 常规提需和试新提需的“一键同步到飞书表格”按钮改为新窗口打开，原运营后台页面会保留。
- 常规提需表单强制携带 `view=regular`，试新相关表单强制携带 `view=trial`。
- 同步成功返回飞书地址前，服务端先把当前 view 写回对应模块，避免全局状态残留。
- 继续保留 v0.3.8 的试新上传后自动新增提需记录能力。

验证记录：

- 常规/试新同步按钮 HTML 均包含 `formtarget="_blank"`。
- 试新上传接口仍能将图片解析结果写入下方提需表记录。
- `PYTHONPATH=. pytest tests -q`：82 passed。

## v0.3.8 - 飞书外跳、试新记录与价值观入库修复

日期：2026-06-08

阶段目标：

- 修复页面实测发现的同步后页面失败/不跳飞书、试新上传未形成提需记录、审批价值观未进入价值观大师规则库、同步记录不可见的问题。

已完成：

- “一键同步到飞书表格”成功后返回飞书在线表格地址，浏览器会直接跳转到飞书。
- 试新上传解析从“单条当前行”升级为“试新提需记录列表”，上传成功后自动新增一条试新提需记录。
- 试新同步改为同步当前试新提需记录列表，成功后清空列表并保留同步事件。
- 审批通过的价值观候选会同步进入“价值观大师”的完整价值观规则库。
- 同步记录页已验证显示真实 `飞书在线表格 / 成功` 记录。

验证记录：

- 上传本地图片后，试新提需表出现 `cat-koi-v037.png` 记录和本地解析备注。
- 试新同步返回 `Location: https://feishu.cn/base/...`。
- 同步记录页出现 `提需同步 / 飞书在线表格 / 成功`。
- 审批候选价值观后，价值观大师规则库可检索到该规则。
- `PYTHONPATH=. pytest tests -q`：82 passed。

## v0.3.7 - 常规/试新真实飞书同步修复

日期：2026-06-08

阶段目标：

- 修复页面测试中发现的常规提需真实飞书同步失败、试新缺少同步按钮、上传解析结果呈现位置不符合预期的问题。

已完成：

- 多维表格写入时自动过滤普通文本形式的 `图片本身` 字段，避免附件字段 `AttachFieldConvFail`。
- 常规提需真实飞书同步已用业务 payload 验证成功。
- 试新提需表新增“一键同步到飞书表格”按钮。
- 试新上传解析结果明确写入下方试新提需表；右侧只展示解析状态，不再作为主要结果承载区。
- 服务端新增 `/sync_trial_feishu`，试新同步成功后重置试新表并写入同步记录。
- 同步记录已验证新增成功记录。

验证记录：

- 常规真实飞书同步：`code 0`，`record_count 1`。
- 试新真实飞书同步：`code 0`，`record_count 1`。
- `PYTHONPATH=. pytest tests -q`：82 passed。

## v0.3.6 - 飞书真实连接诊断与多维表格适配

日期：2026-06-08

阶段目标：

- 使用用户提供的真实飞书应用和在线表格信息进行联调，定位真实飞书写入链路的外部阻塞点。

已完成：

- 本机 `.env` 已写入真实飞书配置，并通过 `.gitignore` 保证不会提交。
- 飞书客户端已能读取真实配置并创建 Real client。
- 修复 Python `urllib` SSL 证书链问题，默认 HTTP transport 改为 `requests`。
- 识别 `FEISHU_SHEET_RANGE` 以 `tbl` 开头时自动走飞书多维表格/Base `records/batch_create` API。
- 添加飞书多维表格写入路径测试，保证 table id 不会误走 Sheets `values_append`。
- 真实请求已打到飞书开放平台，当前阻塞为应用权限缺失：需要开通 `bitable:app` 或 `base:record:create`。

当前限制：

- 飞书开放平台返回 `99991672 No permission`，应用尚未开通多维表格写入所需权限。
- 用户需要在飞书开放平台为应用开通 `bitable:app` 或 `base:record:create`，并确保目标表格授权给该应用。

验证记录：

- `PYTHONPATH=. pytest tests/test_external_adapters.py -q`：8 passed。

## v0.3.5 - 真实飞书门禁、试新图片上传与 Agent Eval 重构

日期：2026-06-05

阶段目标：

- 修复“同步成功但找不到飞书表格”的误导问题，让提需同步必须连接真实飞书；同时补齐试新本地图片上传、同步记录自动更新和更像工程项目的 Agent/RAG 评测面板。

已完成：

- 新增 `.env.example`，列出真实飞书连接所需的 App ID、App Secret、Spreadsheet Token、Sheet Range。
- `.gitignore` 忽略 `.env`，避免误提交飞书密钥。
- `RealFeishuClient` 支持在未提供 `FEISHU_ACCESS_TOKEN` 时，用 App ID/App Secret 自动获取 `tenant_access_token`。
- 提需表“一键同步到飞书表格”改为真实飞书门禁：未配置真实飞书时不会清空提需表，并显示缺失配置。
- 同步记录改为从 SQLite 动态读取，提交提需同步后自动新增成功/失败记录。
- 试新提需新增真实本地图片上传入口，支持 `multipart/form-data`，上传后保存图片并回填提需表解析结果和预览。
- 新增 `TrialImageUploadService`，把上传保存/本地解析从服务端拆出，后续接多模态 LLM 时只替换解析适配层。
- 新增 `AgentEvalSuite`，评测页展示 eval dataset、case 明细、metric 阈值、pass/fail、judge reason。
- Agent 评测新增 Context Precision、Context Recall、Tool Correctness、Plan Adherence、Step Efficiency，借鉴 AgentOps、RAGAS、DeepEval、TruLens 的分层评测思路。
- Agent 评测页的 `feishu.write_table` 仅作为 trace dry-run 展示，不会因为打开评测页而写入真实飞书。

当前限制：

- 真实飞书需要用户在本机 `.env` 填写凭证，并在飞书开放平台授予电子表格读写权限。
- 试新上传已可用，但图片理解仍是本地规则适配层，不是视觉 LLM。
- Agent Eval 是本地可解释 eval suite，尚未接真实 LLM-as-Judge provider。

验证记录：

- `PYTHONPATH=. pytest tests -q`：77 passed。

## v0.3.4 - 提需同步、分析持久化与 RAG 评测补齐

日期：2026-06-05

阶段目标：

- 让当前页面从“能看”继续往“能测试核心业务动作”推进，重点补齐提需同步、试新模拟上传、数据分析保存、价值观候选池反馈和 RAG 评测。

已完成：

- 常规提需表的运营 tag 字段支持编辑和保存。
- 常规提需表新增“一键同步到飞书表格”按钮；同步成功后清空当前提需表，并显示“同步成功，当前已完成提需X条”。
- 试新提需新增可点击的“模拟上传并解析”流程，分别支持参考图解析和好图衍生模式，便于本地验证。
- 试新提需保存逻辑补齐运营 tag 字段，保证常规/试新核心字段一致。
- 数据分析大师新增保存入口，图片明细备注、周期内容分析、下一步 todo 可以在当前服务进程内持久化。
- 数据分析第一行补齐 CD 历史均值，以及 AI 历史均值和 AI OKR。
- 多模态底座页面新增“已审批价值观规则”和 “HITL Memory”展示，运营点击候选规则通过后可以立即看到结果。
- 新增 `TruLensRAGEvaluator` 本地适配层，把 Context Relevance、Groundedness、Answer Relevance 接入 Agent 评测页。
- README 明确说明当前版本没有接真实 LLM/视觉语言模型，不能声称模型本身具备真实多模态能力。

当前限制：

- 飞书真实同步需要配置个人飞书开放平台凭证；未配置时使用 Mock CSV fallback。
- TruLens 评测当前是本地 TruLens-style 指标适配层，不依赖真实 TruLens provider。
- 试新上传仍是模拟图片位，主要用于验证 workflow；后续可接真实图片上传和多模态 LLM。

验证记录：

- `PYTHONPATH=. pytest tests -q`：71 passed。

## v0.3.3 - 价值观候选池 HITL 审核闭环

日期：2026-06-05

阶段目标：

- 让价值观候选池不只停留在展示和程序接口，而是能在页面上由运营点击审核通过，形成可演示的 HITL 闭环。

已完成：

- 多模态底座页面的价值观候选池新增“运营审核”操作列。
- 每条候选价值观支持填写/保留人工备注，并点击“通过”。
- 服务端新增 `/approve_value_candidate` action，调用 `approve_value_candidate`。
- 审核通过后写入固定价值观规则库和 HITL memory。
- Agent 评测页的价值观候选通过率会随审批结果变化。

验证记录：

- `PYTHONPATH=. pytest tests -q`：62 passed。

## v0.3.2 - CMS/MCP-like Adapter 与真实飞书请求骨架

日期：2026-06-05

阶段目标：

- 补齐生产环境中最容易被面试追问的外部系统适配：CMS 库存、MCP-like 工具协议、飞书真实写入请求骨架。

已完成：

- 新增 `MockCMSClient`：模拟公司 CMS 全局未分发素材库，支持按运营 tag 查库存、按国家/JS分类检索素材、识别低库存 tag。
- 新增 `MCPToolAdapter`：以 MCP-like manifest 形式暴露 `cms.query_inventory`、`cms.search_assets`、`cms.low_stock_tags`。
- 增强 `RealFeishuClient`：按飞书官方电子表格追加数据接口构造 `POST /open-apis/sheets/v2/spreadsheets/:spreadsheetToken/values_append` 请求。
- 飞书客户端保留可注入 transport，测试不打真实外网；缺少 `FEISHU_APP_ID/FEISHU_APP_SECRET/FEISHU_SPREADSHEET_TOKEN/FEISHU_ACCESS_TOKEN` 时自动降级 Mock CSV。
- Agent trace 接入外部工具链，展示 `cms.query_inventory` 和 `feishu.write_table`。
- Agent 评测页新增 `CMS/MCP适配状态`、`飞书同步模式`。

当前限制：

- MCP-like adapter 是本地协议化工具层，不是独立 MCP Server 进程。
- 飞书真实写入需要用户自己配置开放平台应用权限、电子表格权限和 access token。
- CMS 仍为本地 mock，不连接公司真实 CMS。

验证记录：

- `PYTHONPATH=. pytest tests -q`：61 passed。
- `http://127.0.0.1:5190/?country=日本&view=eval`：页面显示 CMS/MCP 适配状态、飞书同步模式和完整 tool calls。

## v0.3.1 - 大规模模拟数据与 Tool/Skill Runtime 补齐

日期：2026-06-05

阶段目标：

- 补齐 v0.3.0 中还停留在计划层的“大规模数据生成”和“显式 function calling / skill library”能力。

已完成：

- 新增 `SyntheticDataGenerator`：支持按国家和周数生成历史回收数据。
- 每个国家每周固定生成 139 条记录，支持日本/法国双国家数据集。
- 每条模拟数据包含 `image_id`、`image_url`、`local_image_path`、`thumbnail_path` 和本地图片占位文件。
- 模拟数据遵守固定 JS 分类枚举，并使用日本/法国阈值自动生成多维度等级与 SABCD 等级。
- 新增 `ToolRegistry`：统一注册和调用工具，返回标准 `ToolResult`。
- 新增 `SkillLibrary`：显式定义常规提需、试新提需、价值观大师、价值观候选挖掘、数据分析等业务 Skill 及其 required tools。

当前限制：

- 大规模图片目前使用本地 1px PNG 占位图，主要用于验证链路和页面字段；后续可替换为生成式 mock 图或真实图片 URL。
- Tool/Skill 已成为显式模块，但 Orchestrator 仍是轻量本地实现，尚未接真实 MCP Server。

验证记录：

- `PYTHONPATH=. pytest tests -q`：58 passed。

## v0.3.0 - 多模态 Agent Runtime 工程化升级

日期：2026-06-05

阶段目标：

- 将项目从页面原型升级为可讲工程实现的多模态内容运营 Agent 系统。
- 以真实风格 Excel 样表和审核手册为输入，补齐图片抽取、等级校验、多模态画像、价值观候选池、相似好坏图证据、HITL memory 和 Agent eval/trace。

已完成：

- 新增真实 Excel 导入器：读取 `图片等级、图片本身、图片ID、图片URL、分发位置、多维度等级、开图率、完成率、平均完成时长、运营tag、主体tag、JS分类、图片来源、备注、分发日期、分发周期`。
- 支持 WPS/Excel `DISPIMG` 单元格图片：解析 `xl/cellimages.xml`，将图片解压为本地文件，并写入 `local_image_path/thumbnail_path`。
- 固定 JS 分类枚举：`houses/home/food/flowers/pets/animal/travel/ontheway/zen/objects/patterns/handcrafted/streetview/human`。
- 新增日本/法国等级阈值与 SABCD 校验逻辑：按开图率、完成率、平均完成时长生成多维度等级和图片等级。
- 新增 SQLite 仓库：保存历史图片、HITL memory、已审批价值观规则。
- 新增 Redis 缓存抽象：Redis 不可用时自动降级到 Python 内存缓存。
- 新增飞书客户端抽象：缺少真实飞书密钥时导出 Mock CSV，后续可接真实飞书 API。
- 新增多模态底座：`ImageFeature`、`ImageProfile`、图片结构化特征、caption、历史指标融合。
- 新增相似历史好图/坏图检索：价值观判断可以展示 S/A 证据和 C/D 风险参考。
- 新增价值观候选池：从 SA/CD 历史样本中生成 `pending_review` 候选规则，运营通过后写入固定规则和 memory。
- 新增审核规则检索与规则引擎：从 `拼图审核手册.docx` 召回红线/黄线依据，给出风险等级、原因和修改建议。
- 新增 Agent trace/eval：记录 plan、skill、tool calls、observations、context、memory hits、eval metrics。
- 新增页面入口：`多模态底座 🧠` 和 `Agent 评测 🧪`。

当前限制：

- 当前多模态特征抽取为本地规则/结构化模拟，不声称接入真实视觉大模型。
- 真实飞书客户端只完成接口预留；没有密钥时使用 Mock CSV fallback。
- SQLite/Redis/飞书/MCP-like adapter 已形成工程接口，但尚未接真实公司 CMS。
- 大规模 12 周 × 139 条/国家的数据生成器尚未展开；本版优先完成真实样表导入和 Agent runtime 骨架。

验证记录：

- `PYTHONPATH=. pytest tests -q`：54 passed。

## v0.2.2 - AI率 OKR 规则修正

日期：2026-06-05

阶段目标：

- 修正首页 AI 指标口径，从“AI占比”改回“AI率”，并按业务规则显示颜色。

已完成：

- 首页文案改回 “本季度累计 AI率 / OKR”。
- AI率 OKR 数值保持黑色。
- AI率低于 OKR 时显示绿色。
- AI率等于或超过 OKR 时显示红色。
- AI率超过 OKR 且差距大于 10 个百分点时显示红色感叹号。

验证记录：

- `PYTHONPATH=. pytest tests -q`：28 passed。

## v0.2.1 - 指标颜色语义修正

日期：2026-06-05

阶段目标：

- 修正首页和数据分析大师中的指标颜色语义，让运营判断更直观。

已完成：

- 数据分析大师：SA 占比同比上升显示绿色，下降显示红色。
- 数据分析大师：CD 占比和 AI 占比同比上升显示红色，下降显示绿色。
- 首页：本季度累计 SA/AI 占比的 OKR 数值固定为黑色。
- 首页：实际占比达到/超过 OKR 时显示绿色，未达到 OKR 时显示红色。
- 首页：实际占比与 OKR 差距大于 10 个百分点时，追加红色感叹号提醒。
- 数据分析大师：将 “AI率” 文案统一为 “AI占比”。

验证记录：

- `PYTHONPATH=. pytest tests -q`：27 passed。

## v0.2.0 - 关键交互修复与 PRD 对齐

日期：2026-06-05

阶段目标：

- 修复 v0.1.0 中“按钮点击后页面打不开/功能不生效”的问题。
- 让 Python 版更接近已通过的 PRD 原型，补齐核心业务动作和必要模拟数据。

已完成：

- 首页：本周工作流增加图标，工作流内容和今日待办内容改为可编辑文本框。
- 首页：节日提需建议改为按钮展开，不再默认铺在页面下方。
- 常规提需：修复已分发图片“加入提需”后页面打不开的问题。
- 常规提需：修复“AI生成描述”功能，点击后会批量写入主体描述。
- 常规提需：低库存爆款红色置顶，低库存稳定款黄色展示，其他正常展示。
- 试新提需：修复“价值观大师”按钮，点击后会写入价值观匹配度。
- 试新提需：增加模拟上传区域、参考图 A/B/C、好图衍生说明，使两个模式更接近 PRD。
- 试新提需：提需表字段加宽，张数/需求等级/加工方式使用更容易看见的小输入控件。
- 试新提需：图片本身字段改为图片预览样式，不再只是一句文字。
- 数据分析大师：新增 Python 渲染的 SVG 折线图，并将周期内容分析/下一步 todo 移到页面底部。
- 价值观大师：补充日本/法国价值观规则，覆盖文化真实性、版权风格风险、宗教政治敏感、主体清晰度、构图可拼性、AI 质量、节日适配等。
- 排图工作台：修复“替换”按钮，点击后会替换为未分发候补图，并保留原分发位置。
- 全局：每个功能页标题处保留对应图标，例如常规提需 📦、试新提需 ✨、数据分析 📈。
- 服务端：修复 POST 后重定向中文 URL 导致 `UnicodeEncodeError` 的问题。

当前限制：

- 仍然坚持纯 Python，因此没有使用 JavaScript 实现拖拽上传、双击单元格编辑或无刷新交互。
- 上传图片区域目前是模拟区域，不读取真实图片文件。
- 首页工作流/待办可编辑但暂存在内存里，服务重启后会恢复默认模拟数据。

验证记录：

- `PYTHONPATH=. pytest tests -q`：24 passed。
- 已用真实 POST 验证：加入提需、AI生成描述、价值观大师、排图替换均可返回页面并修改状态。
- `http://127.0.0.1:5188/`：本地页面可访问。

## v0.1.0 - Python 版业务原型基线

日期：2026-06-05

阶段目标：

- 将已通过初审的 PRD 原型转成纯 Python 项目，方便在 VSCode 里阅读和修改。
- 保留真实业务结构，使用模拟数据，不接入公司内部 CMS、飞书或真实业务资产。

已完成：

- 建立纯 Python 项目结构：`puzzle_ops/models.py`、`puzzle_ops/data.py`、`puzzle_ops/agents.py`、`puzzle_ops/renderer.py`、`puzzle_ops/server.py`。
- 实现日本/法国国家隔离：首页指标、任务、节日、分类、运营 tag、历史图、分析数据均按国家区分。
- 实现常规提需流程：分类 -> 完整中文运营 tag + 库存 -> 历史已分发图 -> 批量提需表。
- 实现试新提需流程：参考图解析提需、好图衍生提需、价值观大师写入价值观匹配度。
- 实现数据分析大师：SA/CD/AI 指标、图片来源、5/10 分发位标红、AI 分析备注。
- 实现价值观大师：S/A/B/C/D 按钮筛选预测图，规则库折叠展示。
- 实现排图工作台：按周一到周日展示每日 10 张推荐排图，区分工作日/周末允许分发位置。
- 增加测试覆盖：核心 Agent、页面渲染、服务端参数防御，共 14 个测试。

当前限制：

- 为了满足“全部 Python”要求，页面采用 Python 服务端渲染，没有使用 JavaScript，所以交互不如 HTML PRD 原型丝滑。
- 目前数据为内置模拟数据，还没有接入真实图片上传、真实模型、真实 CMS 或飞书 API。
- 表格修改采用输入框/下拉框保存，不是 PRD 原型里的双击编辑形态。

验证记录：

- `PYTHONPATH=. pytest tests -q`：14 passed。
- `http://127.0.0.1:5188/`：本地页面可访问。

下一阶段建议：

- v0.2.0：优先修复你发现“无法实现/不如 PRD”的功能点。
- v0.3.0：补充简历版项目介绍、面试 Q&A、核心代码讲解文档。
- v0.4.0：如需要展示，可上传到 GitHub 私有仓库或公开仓库。
