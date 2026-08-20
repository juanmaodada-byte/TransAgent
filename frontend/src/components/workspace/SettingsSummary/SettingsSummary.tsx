/**
 * SettingsSummary — 右栏"当前配置"预览
 * ===================================
 * 设置视图下替代文件阅览面板：只读展示已保存的 LLM 配置（脱敏）。
 */

import type { LLMSettings } from '../../../types';
import { Button, Icon, KeyRound, X } from '../../ui';
import './SettingsSummary.css';

export interface SettingsSummaryProps {
  settings: LLMSettings | null;
  onClose: () => void;
}

export function SettingsSummary({ settings, onClose }: SettingsSummaryProps) {
  return (
    <aside className="settings-summary">
      <div className="settings-summary-header">
        <span className="settings-summary-title">
          <Icon icon={KeyRound} size={15} />
          当前配置
        </span>
        <Button
          className="settings-summary-close"
          variant="ghost"
          size="icon"
          onClick={onClose}
          title="收起"
        >
          <Icon icon={X} size={15} />
        </Button>
      </div>

      <div className="settings-summary-body">
        {!settings ? (
          <div className="settings-summary-empty">
            <p>配置加载中…</p>
          </div>
        ) : (
          <>
            <dl className="ss-list">
              <div className="ss-item">
                <dt>服务商</dt>
                <dd>{providerLabel(settings, settings.primary.provider)}</dd>
              </div>
              <div className="ss-item">
                <dt>模型</dt>
                <dd>{settings.primary.model || '—'}</dd>
              </div>
              <div className="ss-item">
                <dt>API Key</dt>
                <dd>{settings.primary.has_key ? settings.primary.key_masked : '未设置'}</dd>
              </div>
              <div className="ss-item">
                <dt>接口地址</dt>
                <dd className="ss-url">{settings.primary.base_url || '—'}</dd>
              </div>
            </dl>
            <p className="settings-summary-note">
              保存后新翻译立即生效。密钥仅保存在本地，不会回显完整内容。
            </p>
          </>
        )}
      </div>
    </aside>
  );
}

/** 服务商 id → 中文名 */
function providerLabel(settings: LLMSettings, providerId: string): string {
  return settings.providers.find((p) => p.id === providerId)?.label ?? providerId;
}
