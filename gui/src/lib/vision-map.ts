/**
 * J2：模型多模态（图片）能力表 —— 纯静态枚举（2026-08-27 定稿，最近更新 2026-09-10）。
 *
 * 范围（用户裁定）：**能生成文本的聊天模型**——只回答"text-to-text 还是 any-to-text
 * （含图片输入）"；图像/视频生成、语音系不在本表范围。
 *
 * 形态：一张精确型号 → 布尔的表。每个型号独立成行、独立对照官方文档核实，无任何
 * 推断规则——不按产品线、不看命名、不按厂家兜底。厂家随时变卦（上一代多模态、
 * 下一代纯文本；coder 线哪天主打前端设计支持视觉）都不影响已核实的行，新型号
 * 核实后加行即可。
 *
 * 未收录型号（含一切未来新型号）：**默认放行**。代价不对称是这么裁定的依据——
 * 文本模型收到图，最坏是一次可见的 API 报错（信息明确、可恢复）；视觉模型被误拦，
 * 是无声的功能残废（用户不知道为什么加不了图）。漏收 DENY 行的代价 = 一次报错，
 * 漏收 ALLOW 行的代价 = 零（放行恰好正确）。
 *
 * 仅存的两条"身份归一"不是能力推断：① OpenRouter 的 "作者/" 前缀剥离（同一型号的
 * 聚合写法）；② 日期后缀剥离（厂商文档口径：日期 ID 是基名型号的快照别名，
 * gpt-4o-2024-11-20 就是 gpt-4o；豆包用六位式 -260628，同理）——归一后仍查同一张表。
 *
 * 维护：新型号上线 → 查官方文档 → 表里加一行（DENY 侧优先级高：用户真在用的
 * 文本聊天型号收全，报错才少）。调研底稿与各家官方链接：docs/archive/vision-map-research.md。
 */
export const VISION_NAME_RE = /vision|-vl|vlm|omni|multimodal|4v\b|\.?\d+v\b/;
// ↑ 不参与能力判定（本表已无规则层）——仅 factory.ts 的 DeepSeek 适配器分派在用，
// 且 2026-09-10 起与 modelSupportsVision 精确表并用（deepseek-flash/V4.1 原生多模态但命名
// 无 vision 字样，单靠本正则会误路由；误路由无害：DeepSeek API 本就是 OpenAI 兼容格式）。

// ---------------------------------------------------------------------------
// 精确型号枚举表：true = 接受图片输入（any-to-text）；false = 纯文本（text-to-text）
// ---------------------------------------------------------------------------
const MODEL_VISION: Readonly<Record<string, boolean>> = {
  // ---- OpenAI（官方 Models 页 "All latest OpenAI models support text and image input"）----
  "gpt-6-astra": true, // 2026-09-03 发布、09-05 起 API 全量开放，官方型号页 Input: Text, image（2026-09-05 核实）
  "gpt-5.6-sol": true,
  "gpt-5.6-sol-ultra": true,
  "gpt-5.6-terra": true,
  "gpt-5.6-luna": true,
  "gpt-5.6-luna-mini": true,
  "gpt-5.6-terra-mini": true, // 天体系可能存在的 mini 变体（OpenAI 官方未明确排除；未收录→放行恰好正确）
  // ↑ 2026-07-09 全面上线，三档全支持图片（Roboflow 独立实测全档过检测/计数/OCR；
  //   Sol 被评为"OpenAI 最强视觉型号"）。另有 ultra 变体（DeepLearning.AI The Batch）
  "gpt-5.5": true,
  "gpt-5.4": true,
  "gpt-5.3-codex": true,
  "gpt-5.2": true,
  "gpt-5.1": true,
  "gpt-5.1-mini": true,
  "gpt-5.1-codex": true,
  "gpt-5.2-mini": true,
  "gpt-5.2-codex": true,
  "gpt-5.3-codex-mini": true,
  "gpt-5.4-mini": true,
  "gpt-5.5-mini": true,
  "gpt-5.5-codex": true, // 5.x 系 mini/codex 变体（官方 Models 页全系 same-modality）
  "gpt-5": true,
  "gpt-5-pro": true,
  "gpt-5-mini": true,
  "gpt-5-nano": true, // 5 基础系 2026-12-11 停服
  "gpt-4.1": true,
  "gpt-4.1-mini": true,
  "gpt-4.1-nano": true,
  "gpt-4o": true,
  "gpt-4o-mini": true,
  "o4-mini": true,
  o3: true,
  "o3-pro": true,
  o1: true,
  "o1-pro": true,
  "o1-mini": true,
  "o3-mini": false, // 官方页明确 "Image: Not supported"
  "gpt-3.5-turbo": false, // 纯文本，2026-09/10 陆续停服
  "gpt-4": false,
  "gpt-4-turbo": false, // 旧快照系 2026-10-23 停服（turbo 历史有 vision，停服口径下拦了无害）
  "gpt-oss-120b": false,
  "gpt-oss-20b": false, // 开放权重，官方页仅文本/推理/工具

  // ---- Anthropic（官方 "All current Claude models support text and image input"；3.5-haiku 与远古系例外）----
  "claude-fable-5-1": true, // 2026-09-01 上线（Fable 5 继任者），官方文档 text & image input（2026-09-04 核实）
  "claude-fable-5": true,
  "claude-mythos-5": true,
  "claude-opus-5": true,
  "claude-opus-4.8": true,
  "claude-opus-4.7": true,
  "claude-opus-4.6": true,
  "claude-opus-4.5": true,
  "claude-sonnet-5": true,
  "claude-sonnet-4.6": true,
  "claude-sonnet-4.5": true,
  "claude-haiku-4.5": true,
  "claude-3-7-sonnet": true,
  "claude-3-7-sonnet-latest": true,
  "claude-3-5-sonnet": true,
  "claude-3-5-sonnet-latest": true,
  "claude-3-opus": true,
  "claude-3-opus-latest": true, // 3.x 历史系有视觉（官方退役，Bedrock/Vertex 存量防御）
  "claude-3-sonnet": true,
  "claude-3-haiku": true,
  "claude-3-5-haiku": false,
  "claude-3-5-haiku-latest": false, // 历史著名的无视觉型号
  "claude-2.1": false,
  "claude-2.0": false,
  "claude-instant-1.2": false, // 远古系

  // ---- Google Gemini（2.5/3.x 文本主线全模态输入，OpenRouter 目录逐型号核过 input_modalities）----
  "gemini-3.8-flash": true, // 2026-09-02 GA（官方博客 + Model Card，多模态输入；2026-09-10 核实）
  "gemini-3.7-flash": true,
  "gemini-3.6-flash": true,
  "gemini-3.5-flash": true,
  "gemini-3.5-flash-lite": true,
  "gemini-3.1-pro": true,
  "gemini-3.1-flash": true,
  "gemini-3.1-flash-lite": true,
  "gemini-3-pro": true,
  "gemini-3-flash": true,
  "gemini-2.5-pro": true,
  "gemini-2.5-flash": true,
  "gemini-2.5-flash-lite": true,
  "gemini-3.5-flash-cyber": false, // 2026-07 限量试点安全专用，二手称纯文本，无官方页可核（存疑拦截）

  // ---- xAI Grok（现役目录聊天型号全部 text+image；Oracle 合作页 + OpenRouter 目录交叉核实）----
  "grok-4.6": true,
  "grok-4.5": true,
  "grok-4.3": true,
  "grok-4": true,
  "grok-4.20": true, // 2026 系列新旗舰（reasoning map 注释提及走 reasoning.enabled 开关，视觉口径同族）
  "grok-4-fast": true,
  "grok-4-1-fast": true, // 遗留 ID 已重定向到多模态 4.3
  "grok-3": true,
  "grok-3-mini": true, // 3-mini 历史纯文本但已退役重定向到多模态（OpenRouter 口径）
  "grok-build-0.1": true,
  "grok-code-fast-1": true, // 连代码专用型也吃图

  // ---- DeepSeek（官方 Vision 指南 + 2026-09-10 V4.1 Flash 发布公告/Pricing 页核实）----
  "deepseek-flash": true, // V4.1 Flash（2026-09-10 发布）：原生多模态视觉理解，官方 API ID 即 deepseek-flash（news260910 + Pricing 页 Vision ✓，2026-09-10 核实）
  "deepseek-v4-flash-vision-exp": true, // 2026-08-21 上线；2026-09-10 起旧名下线、请求由 V4.1 Flash 承接（多模态）
  "deepseek-v4-flash": true, // 同上：2026-09-10 起旧名路由到 V4.1 Flash，收图不再 400
  "deepseek-v4-pro": false, // Pricing 页 Vision: Not supported；2026-09-14 12:00（北京）起官方将路由到 V4.1 Flash，届时本行应改 true
  "deepseek-chat": false,
  "deepseek-reasoner": false,
  "deepseek-coder": false, // 已停用，防御残留配置

  // ---- 智谱 GLM（官方模型概览逐型号核实；glm-5.3-flash 事件实证：多模态命名无规律，逐行枚举）----
  "glm-5.3-flash": true, // 2026-08 末上线：GLM-5 系首个原生多模态（docs vlm/glm-5.3-flash，320B-A18B）
  "glm-5v-turbo": true, // 2026-04，多模态 Coding 基座
  "glm-4.6v": true,
  "glm-4.6v-flash": true,
  "glm-4.6v-flashx": true,
  "glm-4.5v": true,
  "glm-4.1v": true,
  "glm-4.1v-thinking": true,
  "glm-4v-flash": true,
  "glm-4v-plus": true,
  "glm-5.3": false, // 旗舰纯文本（原文"目前仅支持处理文本模态信息"）
  "glm-5.2": false,
  "glm-5.1": false,
  "glm-5": false,
  "glm-5-turbo": false,
  "glm-4.7": false,
  "glm-4.6": false,
  "glm-4.6-flash": false,
  "glm-4.5": false,
  "glm-4.5-air": false,
  "glm-4.5-flash": false,
  "glm-4-flash": false,
  "glm-4-plus": false,
  "glm-4-air": false,
  "glm-4-long": false,

  // ---- 阿里 Qwen/DashScope（官方视觉理解选型页 + 文本生成页；2026-02 起旗舰主线原生视觉）----
  "qwen3.8-max": true,
  "qwen3.8-flash": true, // 官方模型页：输入模态 Image/Text/Video（help.aliyun.com/zh/model-studio/qwen3-8-flash，2026-09-10 核实）
  "qwen3.8-27b": true,
  "qwen3.8-flash-next": true, // flash-next：Qwen4 架构实验预览、原生多模态（ModelScope 官方页）
  "qwen3.7-plus": true,
  "qwen3.7-flash": true,
  "qwen3.7-max-2026-06-08": true, // max 线自此快照起带视觉（官方明文）；更晚快照（如 -2026-07-15）是日期别名→归一到本行
  "qwen3.6-plus": true,
  "qwen3.6-flash": true,
  "qwen3.5-plus": true,
  "qwen3.5-flash": true,
  "qwen3.5-32b": true,
  "qwen3.5-72b": true,
  "qwen3.5-omni": true,
  "qwen3.5-ocr": true,
  "qwen3-vl-plus": true,
  "qwen3-vl-flash": true, // 旧 VL 系在售（"不再首选推荐"）；更多尺寸变体未收录→放行恰好正确
  "qvq-max": true,
  "qvq-plus": true, // 视觉推理"仅思考"系
  "qwen3.7-max": true, // 基名=当前生产别名，跟随最新快照（2026-06-08 起带视觉）；更早的退役快照见下行显式拦截
  "qwen3.7-max-2026-05-20": false, // 更早快照：官方警示"仅支持文本接口"（显式行先于归一命中）
  "qwen3.8-2.4t": false, // 2.4t 档为纯文本（官方文本生成页）
  "qwen3-max": false,
  "qwen3-max-preview": false,
  "qwen-plus": false,
  "qwen-flash": false,
  "qwen-turbo": false, // 商业文本系
  "qwen3-235b-a22b": false,
  "qwen3-30b-a3b": false,
  "qwen3-next-80b-a3b": false, // 老开源文本系（-next 纯文本预览）
  "qwen3-coder-plus": false,
  "qwen3-coder-flash": false, // 代码系：能力标签仅"文本生成"

  // ---- 月之暗面 Kimi/Moonshot（platform.kimi.com 模型列表；视觉已内嵌进主型号）----
  "kimi-k3": true, // 当前旗舰，原生视觉理解（图片+视频）
  "kimi-k2.7-code": true,
  "kimi-k2.7-code-highspeed": true, // 2026 Coding 型号，图片+视频
  "kimi-k2.6": true,
  "kimi-k2.5": true, // k2.5 至 2026-08-31 下线
  "moonshot-v1-8k-vision-preview": true,
  "moonshot-v1-32k-vision-preview": true,
  "moonshot-v1-128k-vision-preview": true, // 2026-08-31 下线
  "kimi-k2": false,
  "kimi-k2-thinking": false,
  "kimi-k2-turbo": false, // 已下线纯文本
  "moonshot-v1-8k": false,
  "moonshot-v1-32k": false,
  "moonshot-v1-128k": false,

  // ---- Mistral（docs.mistral.ai/models；Mistral 3 系全线多模态）----
  "mistral-large-3": true, // 675B MoE（41B 激活），官方定位 "general-purpose multimodal model"
  "mistral-medium-3": true,
  "mistral-medium-3.1": true,
  "mistral-medium-3.5": true, // 3.x 系全线 text+image
  "mistral-small-3.1": true,
  "mistral-small-3.2": true, // 3.x 小型系，同上
  "pixtral-large": true,
  "pixtral-12b": true, // 视觉专用线
  "mistral-7b": false,
  "mistral-nemo": false,
  "mixtral-8x7b": false,
  "mixtral-8x22b": false, // 旧代纯文本（已退役）
  codestral: false,
  "codestral-latest": false, // 代码专用，纯文本

  // ---- Meta Muse（Meta Superintelligence Labs 2026 自研旗舰；ai.meta.com 官方博客）----
  "muse-spark-1.1": true, // 多模态推理（工具调用/计算机操控/代码/截图 UI 理解）
  "muse-spark": true, // 原始版本（2026-04 首发），同基座
  "muse-glimmer": true, // 30B 开源本地智能体，多模态（截图/截图调试）

  // ---- 小米 MiMo（mimo.mi.com 模型页；V2.5 系 2026-06-30 起全面替代 V2）----
  "mimo-v2.5": true, // 310B MoE，原生全模态（图像/视频/音频/文本）
  "mimo-v2.5-pro": true, // 旗舰推理，同基座多模态
  "mimo-v2-omni": true, // 全模态基座（文本+视觉+语音），256K
  "mimo-vl": true, // 视觉语言版（V2 系，已切换到 V2.5 但存量可能存在）
  "mimo-7b": false, // 纯文本推理（数学/代码强化训练，开源 2025）

  // ---- 新加坡 Agnes（Sapiens AI 实验室；agnes-ai.com 自称 "full-modality"）----
  "agnes-2.5-pro-alpha": true, // 多模态推理模型（Artificial Analysis 2026-07-24 上线，Apache 2.0）
  "agnes-2.5-flash": true, // 免费编程模型，平台口径全模态
  "agnes-seallm-8b": false, // 东南亚语言优化开源 8B，纯文本（Hugging Face 描述）

  // ---- MiniMax（platform.minimax.io/docs/release-notes/models；OpenRouter 排行头部常客）----
  "minimax-m3": true, // 428B MoE，原生多模态（text+image+video→text），2026-05/06 上线
  "minimax-m2.5": true, // OpenRouter 排行 #1 常客（2.45T tokens），多模态
  "minimax-h3": true, // 全模态视频理解（text+image+video+audio），新旗舰
  "abab6.5s-chat": false,
  "abab5.5-chat": false, // 旧文本系列（逐步淘汰中，防御存量）

  // ---- 百度文心 ERNIE（千帆平台 cloud.baidu.com/doc/qianfan；2026-08-20 更新）----
  "ernie-5.0": true,
  "ernie-5.1": true, // 原生全模态（文本/图像/音频/视频统一建模），旗舰
  "ernie-x1.1": true, // 推理增强，多模态能力进一步增强
  "ernie-4.5-turbo-128k": false, // 128K 纯文本 Turbo
  "ernie-4.5-vl-28b-a3b": true, // 多模态 MoE（28B 总/3B 激活），思考/非思考双模
  "ernie-4.5-vl-28b-a3b-thinking": true, // 多模态推理（视觉思维链），2025-11 开源
  "ernie-4.5-21b-a3b": false,
  "ernie-4.5-300b-a47b": false, // 开源 4.5 文本系

  // ---- 昆仑万维 天工 Skywork（开源为主；经 OpenRouter/SiliconFlow 等第三方 API 调用）----
  "skywork-r1v": true, // 38B 多模态思维链推理（MMMU 69.0 逼近 GPT-4o），图像+视频理解
  "skywork-r1v-3": true, // 跨模态推理（视觉推理迁移融合）
  "skywork-13b-mm": true, // 旧多模态版
  "skywork-13b-base": false,
  "skywork-13b-math": false, // 开源文本系

  // ---- TII Falcon（阿布扎比技术创新研究院；falconllm.tii.ae）----
  "falcon-h1-34b": true,
  "falcon-h1-14b": true,
  "falcon-h1-9b": true, // Hybrid 混合架构，长推理
  "falcon-h1-7b": true,
  "falcon-h1-3b": true,
  "falcon-h1-1b": true,
  "falcon-h1-0.5b": true,
  "falcon-h1-arabic": true, // 阿拉伯语旗舰，混合推理
  "falcon-perception": true, // 多模态感知（OCR/图表/视觉理解）
  "falcon-3-10b": false,
  "falcon-3-7b": false,
  "falcon-3-2b": false,
  "falcon-3-1b": false, // Falcon 3 文本系

  // ---- 零一万物 01.AI Yi（01.ai / api.lingyiwanwu.com；OpenRouter 上 01-ai/ 前缀）----
  "yi-vision-v2": true, // 多模态理解（多图分析）
  "yi-vision": true, // 原始视觉版
  "yi-lightning": false, // 100B MoE 旗舰，纯文本（LMSYS 全球第六/中国第一）
  "yi-1.5-34b": false,
  "yi-1.5-9b": false, // 开源文本系

  // ---- Cohere（docs.cohere.com/docs/models；企业场景导向）----
  "command-a-plus": true, // 首个多模态推理模型（图片理解+推理），旗舰
  "command-a-vision": true, // 企业多模态（文档/幻灯/图表/图像）
  "command-a-reasoning": true, // 首个推理模型（Cohere 口径含视觉推理能力）
  "command-a": true, // 主力（文档说明含图片输入）
  "command-r-plus": false,
  "command-r": false, // RAG 优化，纯文本
  "command-r7b": false, // 小型 RAG

  // ---- Perplexity Sonar（docs.perplexity.ai；联网搜索型 API）----
  "sonar-pro": true, // 旗舰，支持图片上传
  "sonar-reasoning-pro": true, // 推理+搜索，支持图（think 段后 JSON 输出）
  "sonar-reasoning": true, // 推理+搜索
  "sonar-deep-research": true, // 深度研究
  sonar: true, // 基础版（2026-09-27 停服，同样支持图）

  // ---- 腾讯混元 Hunyuan（TokenHub 短名 hy*；OpenRouter/第三方平台用 hunyuan-* 全名）----
  "hunyuan-hy4-preview": false, // 预览版（2026-08-28 发布，770B/49B MoE，1M 上下文）文本/代码为主；多模态留给正式版
  "hy4-preview": false, // TokenHub 短名（tokenhub.tencentmaas.com）
  "hunyuan-hy3": true, // 正式版（2026-07-06 发布，295B MoE 多模态），同 preview 能力
  "hunyuan-hy3-preview": true, // 预览版（存量 ID 防御）
  hy3: true, // TokenHub 短名
  "hy3-preview": true, // TokenHub 短名（2026-08-31 下线，防御存量）
  "hunyuan-turbo": false, // 纯文本（2026-06-22 旧模型下线后仍可通过 TokenHub 调用）
  "hunyuan-pro": false,
  "hunyuan-standard": false, // 文本系

  // ---- 字节豆包 Doubao（火山方舟模型列表 volcengine.com/docs/82379/1554680；2026-08-30 核实）----
  // API ID 用连字符（doubao-seed-1-6，控制台展示名才是点号 doubao-seed-1.6）；带六位日期
  // 快照后缀（如 -260628）由 canonicalSlug 归一剥离。Seed 系 2025-06 起全线多模态。
  "doubao-seed-2-1-pro": true, // 2026-06-23 旗舰：多模态理解+视觉定位（图片 detail 三档/小时级长视频）
  "doubao-seed-2-1-turbo": true, // 同日发布的规模化生产版，效果比肩 pro，支持图片输入
  "doubao-seed-2-0-pro": true, // 2026-02-14：多模态理解+视觉定位，256K
  "doubao-seed-2-0-lite": true, // 2026-05-06 升级为豆包家族首款全模态（视频/图像/音频/文本统一理解）
  "doubao-seed-2-0-mini": true, // 低成本低延迟版，具备多模态理解（360 智脑转售页）
  "doubao-seed-1-8": true, // 2025-12：多模态 Agent（单次视频理解 640→1280 帧）
  "doubao-seed-1-6": true, // 2025-06：多模态深度思考，256K
  "doubao-seed-1-6-thinking": true, // 思考强化变体，同基座多模态
  "doubao-seed-1-6-flash": true, // 极速版（TPOT 10ms），视觉理解比肩友商 Pro（百科/51CTO 实测）
  "doubao-seed-1-6-vision": true, // 视觉专用版（视频理解/Grounding/GUI Agent）
  "doubao-1.5-thinking-pro-vision": true, // 视觉版思考型号
  "doubao-1.5-vision-pro": true,
  "doubao-1.5-vision-lite": true,
  "doubao-1.5-ui-tars": true, // GUI Agent（截图驱动）
  "doubao-1.5-thinking-pro": false, // 文本思考型号（视觉另有 -vision 变体）
  "doubao-1.5-pro-32k": false,
  "doubao-1.5-pro-256k": false, // 1.5 商业文本系
  "doubao-1.5-lite-32k": false,
  "doubao-pro-32k": false, // 官方单页能力标注：输入仅 Text（Image 划除，2026-08-30 渲染核实）
  "doubao-pro-256k": false,
  "doubao-lite-32k": false, // 旧文本系（防御存量）
  // doubao-seed-evolving（2026-06-27 深度思考/Agent/Coding 型）视觉口径未核实 → 未收录默认放行

  // ---- Meta Llama（开源；OpenRouter 上 meta-llama/ 前缀）----
  "llama-4-maverick": true,
  "llama-4-scout": true, // Llama 4 多模态（MoE）
  "llama-3.3-70b-instruct": false,
  "llama-3.1-70b-instruct": false,
  "llama-3.1-8b-instruct": false,
  "llama-3-70b-instruct": false, // Llama 3 系纯文本
};

/** 身份归一（非能力推断）：OpenRouter "作者/" 前缀剥离 + 日期快照别名剥离（厂商口径：日期 ID 是基名快照；\d{6} 为豆包式 -260628） */
function canonicalSlug(modelId: string): string[] {
  let slug = modelId.toLowerCase();
  if (slug.includes("/")) slug = slug.slice(slug.indexOf("/") + 1);
  const stripped = slug.replace(/-(\d{4}-\d{2}-\d{2}|\d{8}|\d{6})$/, "");
  return stripped === slug ? [slug] : [slug, stripped];
}

export function modelSupportsVision(_providerId: string | undefined, modelId: string | undefined): boolean {
  for (const slug of canonicalSlug(modelId ?? "")) {
    const known = MODEL_VISION[slug];
    if (known !== undefined) return known;
  }
  // 未收录（含未来新型号）：默认放行——文本模型收图最坏一次可见报错，误拦视觉模型是无声残废
  return true;
}
