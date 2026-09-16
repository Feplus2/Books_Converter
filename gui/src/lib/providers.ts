// LLM/VLM 提供商预设（照抄 SageRead 列表；supportsReasoning 对应 vlm_client.py 的思考参数映射）
export interface ProviderPreset {
  id: string;
  name: string;
  baseUrl: string;
  signupUrl?: string;
  /** true = 该端点支持思考参数（off/low/medium/high 才有意义）；其余端点 UI 不暴露思考档 */
  supportsReasoning?: boolean;
}

export const PROVIDER_PRESETS: ProviderPreset[] = [
  {
    id: "bigmodel",
    name: "智谱 GLM",
    baseUrl: "https://open.bigmodel.cn/api/paas/v4",
    signupUrl: "https://open.bigmodel.cn/usercenter/apikeys",
    supportsReasoning: true,
  },
  {
    id: "deepseek",
    name: "DeepSeek",
    baseUrl: "https://api.deepseek.com",
    signupUrl: "https://platform.deepseek.com/api_keys",
    supportsReasoning: true,
  },
  {
    id: "dashscope",
    name: "阿里百炼 DashScope",
    baseUrl: "https://dashscope.aliyuncs.com/compatible-mode/v1",
    signupUrl: "https://bailian.console.aliyun.com/#/api-key",
    supportsReasoning: true,
  },
  {
    id: "moonshot",
    name: "Kimi Moonshot",
    baseUrl: "https://api.moonshot.cn/v1",
    signupUrl: "https://platform.moonshot.cn/console/api-keys",
  },
  {
    id: "openai",
    name: "OpenAI",
    baseUrl: "https://api.openai.com/v1",
    signupUrl: "https://platform.openai.com/api-keys",
  },
  {
    id: "openrouter",
    name: "OpenRouter",
    baseUrl: "https://openrouter.ai/api/v1",
    signupUrl: "https://openrouter.ai/settings/keys",
  },
  {
    id: "gemini",
    name: "Gemini",
    baseUrl: "https://generativelanguage.googleapis.com/v1beta/openai",
    signupUrl: "https://aistudio.google.com/apikey",
  },
  {
    id: "grok",
    name: "Grok",
    baseUrl: "https://api.x.ai/v1",
    signupUrl: "https://console.x.ai/team/default/api-keys",
  },
  {
    id: "volcengine",
    name: "火山引擎（豆包）",
    baseUrl: "https://ark.cn-beijing.volces.com/api/v3",
    signupUrl: "https://console.volcengine.com/ark/region:ark+cn-beijing/apiKey",
  },
  { id: "custom", name: "自定义 OpenAI 兼容", baseUrl: "" },
];

export function presetName(id: string): string {
  return PROVIDER_PRESETS.find((p) => p.id === id)?.name ?? id;
}

export function presetOf(id: string): ProviderPreset | undefined {
  return PROVIDER_PRESETS.find((p) => p.id === id);
}
