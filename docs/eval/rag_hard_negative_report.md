# PuzzleOps RAG Citation Hard-Negative Report

## 结论

- 当前是 RAG citation hard-negative 评测报告，不改价值观大师主预测，不改线上等级预测。
- 目标是验证国家价值观/审核规则召回是否真正支撑当前图片判断，并暴露误召回和 hard-negative 问题。

## 范围

- 国家：日本、法国
- Case 数：57
- 阈值：Hit@5 >= 0.8

## 指标

- Hit@5：100%
- MRR@5：97%
- NDCG@5：98%
- Precision@5：20%
- Recall@5：100%
- Hard-negative Top1 率：0%
- Hard-negative TopK 率：22%

## 失败类型

- passed：47
- passed_with_hard_negative_noise：10

## 失败样例

- 日本｜游客 清新插画风（类似《旅行青蛙》或《千与千寻》概念美术），高饱和度清透蓝+暖米白主调，中等明度，竖向构图，强调前中后景层次与光影对比 日本广岛县宫岛（严岛）海岸悬崖步道，晴朗日间海景，含潮间带、飞鸟、远山与城镇轮廓 本土文化符号 low_cultural_confusion no_france_elements no_copyright_risk no_ip_violation A 价值观 审核 风险 真实业务样本：expected=JP_HARNESS_GOLD_f47ac10b-58cc-4372-a567-0e02b2c3d479；retrieved=JP_HARNESS_GOLD_f47ac10b-58cc-4372-a567-0e02b2c3d479、JP_HARNESS_GOLD_6ba7b812-9dad-11d1-80b4-00c04fd430c27、JP_HARNESS_GOLD_6ba7b812-9dad-11d1-80b4-00c04fd430c8、JP_HARNESS_GOLD_6ba7b810-9dad-11d1-80b4-00c04fd430c8、JP_HARNESS_GOLD_6ba7b812-9dad-11d1-80b4-00c04fd430c14；type=passed_with_hard_negative_noise
- 日本｜橘白相间的家猫日 清新治愈系插画风摄影合成图像 阳光明媚的沙滩海岸线，海浪轻拍岸边，沙粒细腻，散落贝壳 季节感治愈 low_copyright_risk no_ip_infringement no_cultural_misappropriation no_france_japan_confusion B 价值观 审核 风险 真实业务样本：expected=JP_HARNESS_GOLD_6ba7b812-9dad-11d1-80b4-00c04fd430c23；retrieved=JP_HARNESS_GOLD_6ba7b812-9dad-11d1-80b4-00c04fd430c23、JP_HARNESS_GOLD_6ba7b812-9dad-11d1-80b4-00c04fd430c18、JP_HARNESS_GOLD_f47ac10b-58cc-4372-a567-0e02b2c3d479、JP_HARNESS_GOLD_6ba7b812-9dad-11d1-80b4-00c04fd430c11、JP_HARNESS_GOLD_6ba7b812-9dad-11d1-80b4-00c04fd430c8；type=passed_with_hard_negative_noise
- 日本｜日式寿喜烧Suk 高饱和暖色调静物摄影，方形构图，前中后景层次分明（锅体近景清晰、食材中景丰富、背景虚化木质纹理），电影感光影，强调食物质感与热气动态 木质餐桌上的铜锅寿喜烧，置于传统木制便携炉（七轮/コンロ）上，蒸汽升腾，背景为暖调木质环境 本土文化符号 无 B 价值观 审核 风险 真实业务样本：expected=JP_HARNESS_GOLD_6ba7b812-9dad-11d1-80b4-00c04fd430c24；retrieved=JP_HARNESS_GOLD_6ba7b812-9dad-11d1-80b4-00c04fd430c24、JP_HARNESS_GOLD_6ba7b810-9dad-11d1-80b4-00c04fd430c8、JP_HARNESS_GOLD_6ba7b812-9dad-11d1-80b4-00c04fd430c8、JP_HARNESS_GOLD_6ba7b812-9dad-11d1-80b4-00c04fd430c14、JP_HARNESS_GOLD_6ba7b812-9dad-11d1-80b4-00c04fd430c21；type=passed_with_hard_negative_noise
- 日本｜严岛神社Itsu 写实摄影风格，高动态范围（HDR）自然光处理，横向构图强调空间纵深 日本广岛县宫岛，春季樱花盛开时节的山海环绕神社景观 本土文化符号 季节感治愈 无 A 价值观 审核 风险 真实业务样本：expected=JP_HARNESS_GOLD_6ba7b812-9dad-11d1-80b4-00c04fd430c27；retrieved=JP_HARNESS_GOLD_6ba7b812-9dad-11d1-80b4-00c04fd430c27、JP_HARNESS_GOLD_6ba7b812-9dad-11d1-80b4-00c04fd430c11、JP_HARNESS_GOLD_6ba7b812-9dad-11d1-80b4-00c04fd430c26、JP_HARNESS_GOLD_f47ac10b-58cc-4372-a567-0e02b2c3d479、JP_HARNESS_GOLD_6ba7b812-9dad-11d1-80b4-00c04fd430c28；type=passed_with_hard_negative_noise
- 法国｜切片牛排配土豆泥 高饱和暖色调静物摄影风格，方形构图，中等明度，深色背景衬托主体，强调前中后景层次与食材质感 木质餐桌上的法式浪漫晚餐场景，背景为丰盛花卉布置（玫瑰、郁金香、洋桔梗等），暖光氛围 生活艺术 复古优雅 自然治愈 低风险：无明显IP/版权元素（如品牌标识 知名菜品特写） 低风险：文化元素明确指向法国（红酒+牛排+花卉礼仪） 无日式混淆 注意：芝士蛋糕形态较通用 非特定法国传统甜点（如tarte Tatin） 但整体语境仍属法式餐饮美学 C 价值观 审核 风险 真实业务样本：expected=FR_HARNESS_GOLD_6ba7b812-9dad-11d1-80b4-00c04fd430c31；retrieved=FR_HARNESS_GOLD_6ba7b812-9dad-11d1-80b4-00c04fd430c31、FR_HARNESS_GOLD_6ba7b812-9dad-11d1-80b4-00c04fd430c32、FR_HARNESS_GOLD_6ba7b812-9dad-11d1-80b4-00c04fd430c45、FR_HARNESS_GOLD_6ba7b812-9dad-11d1-80b4-00c04fd430c47、FR_HARNESS_GOLD_6ba7b812-9dad-11d1-80b4-00c04fd430c44；type=passed_with_hard_negative_noise
- 法国｜海滨餐厅餐桌上的 清新自然主义摄影风格，高动态范围光照，浅景深突出主体，方形构图，中等明度与中饱和度，暖米白+清透蓝主色调 阳光明媚的法国地中海沿岸露天餐厅，木质桌椅配白色藤编座椅，背景为波光粼粼的蓝色海面与金属栏杆 生活艺术 自然治愈 低风险：无版权IP元素（如品牌标识 知名地标如埃菲尔铁塔） 无文化混淆：食材与餐具符合法国南部/地中海饮食文化特征 未混入日式元素（如寿司 酱油碟 竹器） 无敏感符号：无宗教 政治或争议性图像 S 价值观 审核 风险 真实业务样本：expected=FR_HARNESS_GOLD_6ba7b812-9dad-11d1-80b4-00c04fd430c32；retrieved=FR_HARNESS_GOLD_6ba7b812-9dad-11d1-80b4-00c04fd430c32、FR_HARNESS_GOLD_6ba7b812-9dad-11d1-80b4-00c04fd430c31、FR_HARNESS_GOLD_6ba7b812-9dad-11d1-80b4-00c04fd430c37、FR_HARNESS_GOLD_6ba7b812-9dad-11d1-80b4-00c04fd430c47、FR_HARNESS_GOLD_6ba7b812-9dad-11d1-80b4-00c04fd430c45；type=passed_with_hard_negative_noise
- 法国｜一位身着碎花连衣 印象派后期写实油画风格，厚涂笔触（impasto），暖光渲染，强调光影过渡与氛围感；构图采用方形画幅，主体居左，窗框形成天然画中画结构 乡村农舍窗景：木质窗框、阳光洒落的室内角落与开阔田野构成前中后景层次；远处可见石砌农舍、绿丘与树林，属典型的法国乡村（如诺曼底或普罗旺斯边缘）风光 法式乡村 自然治愈 无版权风险（原创风格绘画 无明确IP人物/地标） 无文化混淆（元素纯属西欧乡村语境 无日式符号） 无敏感内容（非政治/宗教/暴力主题） S 价值观 审核 风险 真实业务样本：expected=FR_HARNESS_GOLD_6ba7b812-9dad-11d1-80b4-00c04fd430c38；retrieved=FR_HARNESS_GOLD_6ba7b812-9dad-11d1-80b4-00c04fd430c38、FR_HARNESS_GOLD_6ba7b812-9dad-11d1-80b4-00c04fd430c47、FR_HARNESS_GOLD_6ba7b812-9dad-11d1-80b4-00c04fd430c37、FR_HARNESS_GOLD_6ba7b812-9dad-11d1-80b4-00c04fd430c30、FR_HARNESS_GOLD_6ba7b812-9dad-11d1-80b4-00c04fd430c42；type=passed_with_hard_negative_noise
- 法国｜白人男孩演奏小提 印象派后期写实油画风格，厚涂笔触（impasto），暖光渲染，注重光影层次与质感表现 室内画室，阳光从右侧窗户照入，背景挂有多幅装裱画作 待人工确认价值观 low_copyright_risk no_ip_violation no_cultural_confusion A 价值观 审核 风险 真实业务样本：expected=FR_HARNESS_GOLD_6ba7b812-9dad-11d1-80b4-00c04fd430c42；retrieved=FR_HARNESS_GOLD_6ba7b812-9dad-11d1-80b4-00c04fd430c42、FR_HARNESS_GOLD_6ba7b812-9dad-11d1-80b4-00c04fd430c38、FR_HARNESS_GOLD_6ba7b812-9dad-11d1-80b4-00c04fd430c35、FR_HARNESS_GOLD_6ba7b812-9dad-11d1-80b4-00c04fd430c30、FR_HARNESS_GOLD_6ba7b812-9dad-11d1-80b4-00c04fd430c47；type=passed_with_hard_negative_noise
- 法国｜复古白色皮卡货车 暖色调插画风，高饱和、中等明度，方形构图，清晰主体边界与前中后景层次，类似儿童绘本或旅游宣传插画 法国乡村秋日田园风光：干草堆、成熟麦田、橘子树（实为柑橘类果树）、南瓜、远山与木栅栏 法式乡村 复古优雅 低风险：无明显版权/IP元素（车辆为通用复古设计 非特定品牌商标） 文化混淆风险：低——橘子树在法国南部（如科西嘉 普罗旺斯）真实存在 非日本特有 南瓜与麦田为泛欧洲秋季意象 地域真实性：需注意——法国主产苹果/梨/葡萄 柑橘类多限南部地中海沿岸 但作为艺术化表现可接受 B 价值观 审核 风险 真实业务样本：expected=FR_HARNESS_GOLD_6ba7b812-9dad-11d1-80b4-00c04fd430c44；retrieved=FR_HARNESS_GOLD_6ba7b812-9dad-11d1-80b4-00c04fd430c44、FR_HARNESS_GOLD_6ba7b812-9dad-11d1-80b4-00c04fd430c45、FR_HARNESS_GOLD_6ba7b812-9dad-11d1-80b4-00c04fd430c37、FR_HARNESS_GOLD_6ba7b812-9dad-11d1-80b4-00c04fd430c31、FR_HARNESS_GOLD_6ba7b812-9dad-11d1-80b4-00c04fd430c30；type=passed_with_hard_negative_noise
- 法国｜马卡龙Macar 高质感美食摄影风格；暖米白/暖红主色调；中等明度、中饱和度；方形构图；主体清晰边界，强调酥皮裂纹与夹心层次 浅色背景上的多色马卡龙静物特写，前中后景层次分明，柔和布光 待人工确认价值观 AI生成标识需移除以避免版权/真实性争议 无明显文化混淆风险（马卡龙明确属法国文化符号 非日本） 需注意：若用于商业拼图产品 应确保马卡龙造型不仿冒特定品牌（如Ladurée）专属配色/纹理 D 价值观 审核 风险 真实业务样本：expected=FR_HARNESS_GOLD_6ba7b812-9dad-11d1-80b4-00c04fd430c45；retrieved=FR_HARNESS_GOLD_6ba7b812-9dad-11d1-80b4-00c04fd430c45、FR_HARNESS_GOLD_6ba7b812-9dad-11d1-80b4-00c04fd430c37、FR_HARNESS_GOLD_6ba7b812-9dad-11d1-80b4-00c04fd430c31、FR_HARNESS_GOLD_6ba7b812-9dad-11d1-80b4-00c04fd430c35、FR_HARNESS_GOLD_6ba7b812-9dad-11d1-80b4-00c04fd430c47；type=passed_with_hard_negative_noise

## 决策

- 状态：keep_shadow_repair
- 下一步：人工复核 failed_cases；把 confirmed hard-negative 反馈沉淀为 approved_rag_patch 后再重建索引。
- 简历口径：可写 RAG 评测与 hard-negative 治理闭环；未通过前不写价值观预测效果已稳定。
