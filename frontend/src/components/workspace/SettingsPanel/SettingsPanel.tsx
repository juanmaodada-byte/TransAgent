/**
 * SettingsPanel — 中栏"设置"视图
 * ===============================
 * 在三栏工作区的中栏渲染 API 配置表单（点击左栏"设置"进入，不跳转独立页面）。
 * 仅配置主通道（备选通道由后端按既有配置自动兜底，不在前端设计内）。
 * 保存后通过 onSettingsChange 将最新配置上抛，供右栏 SettingsSummary 展示。
 */

import { useCallback, useEffect, useState } from 'react';
import { isMockMode } from '../../../api/mock';
import { fetchLLMSettings, saveLLMSettings } from '../../../api/client';
import { mockFetchLLMSettings, mockSaveLLMSettings } from '../../../api/mock';
import type { LLMSettings, LLMProvider } from '../../../types';
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
  Check,
  Eye,
  EyeOff,
  Icon,
  KeyRound,
  Loader2,
  Save,
} from '../../ui';
import './SettingsPanel.css';

/** 主通道表单状态 */
interface ChannelForm {
  provider: string;
  model: string;
  api_key: string;
  base_url: string;
}

type SaveState = 'idle' | 'saving' | 'success' | 'error';

/** 提取错误消息（catch 变量为 unknown） */
function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

/** 找 provider（找不到时回退到第一个） */
function resolveProvider(providers: LLMProvider[], providerId: string): LLMProvider {
  return providers.find((p) => p.id === providerId) ?? providers[0];
}

/** 从通道视图还原为表单状态 */
function channelToForm(channel: LLMSettings['primary'], providers: LLMProvider[]): ChannelForm {
  const p = resolveProvider(providers, channel.provider);
  return {
    provider: p.id,
    model: channel.model,
    api_key: '',
    base_url: channel.base_url || p.default_base_url,
  };
}

export interface SettingsPanelProps {
  /** 配置加载/保存成功后上抛，供右栏预览 */
  onSettingsChange?: (settings: LLMSettings) => void;
}

export function SettingsPanel({ onSettingsChange }: SettingsPanelProps) {
  const isMock = isMockMode();

  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [settings, setSettings] = useState<LLMSettings | null>(null);
  const [form, setForm] = useState<ChannelForm | null>(null);

  const [saveState, setSaveState] = useState<SaveState>('idle');
  const [saveMsg, setSaveMsg] = useState('');
  const [showKey, setShowKey] = useState(false);

  const fetchFn = useCallback(
    () => (isMock ? mockFetchLLMSettings() : fetchLLMSettings()),
    [isMock]
  );
  const saveFn = useCallback(
    (payload: Parameters<typeof saveLLMSettings>[0]) =>
      isMock ? mockSaveLLMSettings(payload) : saveLLMSettings(payload),
    [isMock]
  );

  // ── 加载 ──
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchFn()
      .then((data) => {
        if (cancelled) return;
        setSettings(data);
        setForm(channelToForm(data.primary, data.providers));
        setLoadError('');
        onSettingsChange?.(data);
      })
      .catch((e) => {
        if (cancelled) return;
        setLoadError(`读取配置失败：${errMsg(e)}`);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fetchFn]);

  const providers = settings?.providers ?? [];

  const updateForm = useCallback((patch: Partial<ChannelForm>) => {
    setForm((prev) => (prev ? { ...prev, ...patch } : prev));
  }, []);

  /** 切换服务商 → 重置为默认模型与接口地址 */
  const handleProviderChange = useCallback(
    (providerId: string) => {
      const p = resolveProvider(providers, providerId);
      updateForm({
        provider: p.id,
        model: p.models[0],
        api_key: '',
        base_url: p.default_base_url,
      });
    },
    [providers, updateForm]
  );

  // ── 保存（备选通道按既有配置原样提交，后端自动保留） ──
  const handleSave = useCallback(async () => {
    if (!form) return;

    if (!form.model) {
      setSaveState('error');
      setSaveMsg('请选择模型');
      return;
    }
    if (!settings?.primary.has_key && !form.api_key.trim()) {
      setSaveState('error');
      setSaveMsg('请填写 API Key（首次配置必填）');
      return;
    }
    if (!form.base_url.trim()) {
      setSaveState('error');
      setSaveMsg('请填写接口地址');
      return;
    }

    setSaveState('saving');
    try {
      const saved = await saveFn({
        primary: {
          provider: form.provider,
          model: form.model,
          api_key: form.api_key.trim(),
          base_url: form.base_url.trim(),
        },
        backup: {
          provider: settings?.backup.provider ?? form.provider,
          model: settings?.backup.model ?? form.model,
          api_key: '',
          base_url: settings?.backup.base_url ?? form.base_url,
        },
      });
      setSettings(saved);
      onSettingsChange?.(saved);
      setForm((prev) => (prev ? { ...prev, api_key: '' } : prev));
      setSaveState('success');
      setSaveMsg('设置已保存，新翻译将使用此配置');
      setTimeout(() => setSaveState('idle'), 2500);
    } catch (e) {
      setSaveState('error');
      setSaveMsg(`保存失败：${errMsg(e)}`);
    }
  }, [form, settings, saveFn, onSettingsChange]);

  // ── 加载中 / 错误 ──
  if (loading) {
    return (
      <div className="settings-panel">
        <div className="settings-panel-loading">
          <Icon icon={Loader2} className="ui-icon-spin" size={20} />
          <span>加载设置…</span>
        </div>
      </div>
    );
  }

  if (!form) {
    return (
      <div className="settings-panel">
        <div className="settings-panel-loading settings-panel-error">
          <p>{loadError || '无法读取设置'}</p>
          <Button variant="outline" onClick={() => window.location.reload()}>
            重试
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="settings-panel">
      {/* ── 面板头 ── */}
      <div className="settings-panel-head">
        <div>
          <h1 className="settings-panel-title">设置</h1>
          <p className="settings-panel-subtitle">配置翻译智能体的模型服务</p>
        </div>
        <Badge variant={isMock ? 'warning' : 'success'} className="settings-panel-mode">
          {isMock ? 'Mock' : '真实'}
        </Badge>
      </div>

      {/* ── API 配置 ── */}
      <Card className="settings-card">
        <CardHeader>
          <div className="settings-card-title-row">
            <span className="settings-card-icon">
              <Icon icon={KeyRound} size={18} />
            </span>
            <div>
              <CardTitle>API 配置</CardTitle>
              <CardDescription>填写 LLM 服务商的 API Key 与模型，翻译将使用此配置调用模型。</CardDescription>
            </div>
          </div>
        </CardHeader>

        <CardContent className="settings-card-body">
          <div className="channel-grid">
            <div className="settings-field">
              <label className="settings-field-label">服务商</label>
              <select
                className="ui-select"
                value={form.provider}
                onChange={(e) => handleProviderChange(e.target.value)}
              >
                {providers.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.label}
                  </option>
                ))}
              </select>
            </div>
            <div className="settings-field">
              <label className="settings-field-label">模型</label>
              <select
                className="ui-select"
                value={form.model}
                onChange={(e) => updateForm({ model: e.target.value })}
              >
                {providers
                  .find((p) => p.id === form.provider)
                  ?.models.map((m) => (
                    <option key={m} value={m}>
                      {m}
                    </option>
                  ))}
              </select>
            </div>
          </div>

          <div className="settings-field">
            <label className="settings-field-label">API Key</label>
            <div className="api-key-input">
              <input
                className="ui-input api-key-field"
                type={showKey ? 'text' : 'password'}
                value={form.api_key}
                onChange={(e) => updateForm({ api_key: e.target.value })}
                placeholder={
                  settings?.primary.has_key
                    ? `已保存 ${settings.primary.key_masked || '密钥'}，留空则保持不变`
                    : 'sk-…'
                }
                autoComplete="off"
                spellCheck={false}
              />
              <Button
                className="api-key-toggle"
                variant="ghost"
                size="icon"
                onClick={() => setShowKey((v) => !v)}
                title={showKey ? '隐藏密钥' : '显示密钥'}
                icon={<Icon icon={showKey ? EyeOff : Eye} size={16} />}
              />
            </div>
          </div>

          <div className="settings-field">
            <label className="settings-field-label">接口地址</label>
            <input
              className="ui-input"
              value={form.base_url}
              onChange={(e) => updateForm({ base_url: e.target.value })}
              placeholder="https://api.example.com/v1"
              spellCheck={false}
            />
          </div>
        </CardContent>

        <CardFooter className="settings-footer">
          <span className={`settings-save-status ${saveState}`}>
            {saveState === 'saving' && (
              <>
                <Icon icon={Loader2} className="ui-icon-spin" size={14} /> 保存中…
              </>
            )}
            {saveState === 'success' && (
              <>
                <Icon icon={Check} size={14} /> {saveMsg}
              </>
            )}
            {saveState === 'error' && <span className="settings-save-error">{saveMsg}</span>}
          </span>
          <Button
            onClick={handleSave}
            disabled={saveState === 'saving'}
            icon={<Icon icon={Save} size={15} />}
          >
            {saveState === 'saving' ? '保存中…' : '保存设置'}
          </Button>
        </CardFooter>
      </Card>

      <p className="settings-note">
        API Key 仅保存在本地（{isMock ? '浏览器 localStorage' : '服务端 data/user_llm.json'}），不会上传到其他位置。
      </p>
    </div>
  );
}
