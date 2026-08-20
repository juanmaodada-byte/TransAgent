/**
 * WorkspacePage — 三栏项目工作区
 * ===============================
 * 布局：顶条 + 左栏（功能区/项目区） + 中栏（Codex 式对话流 + 底部输入框） + 右栏（文件阅览）。
 * 翻译流程由 useProjectRunner 接管：输入 → 对话消息流 + 项目文件。
 */

import { useEffect, useState, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useProjectsContext } from '../../context/ProjectsContext';
import { LeftPanel } from '../../components/workspace/LeftPanel/LeftPanel';
import { CenterPanel } from '../../components/workspace/CenterPanel/CenterPanel';
import { RightPanel } from '../../components/workspace/RightPanel/RightPanel';
import { SettingsPanel } from '../../components/workspace/SettingsPanel/SettingsPanel';
import { SettingsSummary } from '../../components/workspace/SettingsSummary/SettingsSummary';
import { useProjectRunner } from '../../hooks/useProjectRunner';
import { loadUIState, saveUIState } from '../../storage/projectStore';
import type { ProjectFile } from '../../types/project';
import type { LLMSettings } from '../../types';
import { isMockMode, mockUploadFile } from '../../api/mock';
import { uploadFile } from '../../api/client';
import { detectFormat } from '../../utils/inputText';
import { Badge, Button, Icon, PanelRightOpen, Plus } from '../../components/ui';
import './WorkspacePage.css';

/** 工作区主区视图：对话流 / 设置 */
type WorkspaceView = 'chat' | 'settings';

export function WorkspacePage() {
  const { projectId } = useParams<{ projectId: string }>();
  const navigate = useNavigate();
  const { activeProject, activeProjectId, actions } = useProjectsContext();
  const [uiState] = useState(() => loadUIState());
  const [rightPanelOpen, setRightPanelOpen] = useState(uiState.rightPanelOpen);
  const [selectedFileId, setSelectedFileId] = useState<string | null>(
    uiState.selectedFileId
  );
  /** 中栏视图：对话流 / 设置 */
  const [view, setView] = useState<WorkspaceView>('chat');
  /** 最新 LLM 配置（设置视图右栏预览用） */
  const [llmSettings, setLlmSettings] = useState<LLMSettings | null>(null);
  const isMock = isMockMode();

  // UI 状态记忆（ta.ui.v1）
  useEffect(() => {
    saveUIState({ rightPanelOpen, selectedFileId });
  }, [rightPanelOpen, selectedFileId]);

  // 翻译运行器（SSE → 对话消息 + 项目文件）
  const runner = useProjectRunner();

  // URL 中的项目 id 与当前激活项目不一致时 → 切换；切换项目回到对话视图
  useEffect(() => {
    setView('chat');
    if (projectId && projectId !== activeProjectId) {
      actions.selectProject(projectId);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  const handleToggleRight = useCallback(() => {
    setRightPanelOpen((prev) => !prev);
  }, []);

  /** 进入设置视图：中栏→配置表单，右栏→当前配置预览 */
  const handleOpenSettings = useCallback(() => {
    setView('settings');
    setRightPanelOpen(true);
  }, []);

  /** 新建项目并进入 */
  const handleCreateProject = useCallback(() => {
    const id = actions.createProject();
    navigate(`/projects/${id}`);
  }, [actions, navigate]);

  // ── 输入 → 翻译 ──

  /** 发送文本（粘贴原文） */
  const handleSendText = useCallback(
    async (text: string) => {
      setView('chat');
      const format = detectFormat(text);
      const filename = `pasted-doc.${format === 'md' ? 'md' : 'txt'}`;
      const mime = format === 'md' ? 'text/markdown' : 'text/plain';
      const file = new File([text], filename, { type: mime });
      const uploadFn = isMockMode() ? mockUploadFile : uploadFile;
      try {
        const res = await uploadFn(file);
        if (res.error) {
          console.error('发送失败:', res.error);
          return;
        }
        runner.startTranslation({
          kind: 'paste',
          title: filename,
          fileId: res.file_id,
          sourcePreview: text.slice(0, 500),
        });
      } catch (err) {
        console.error('发送失败:', err);
      }
    },
    [runner]
  );

  /** 上传文件 */
  const handleUploadFiles = useCallback(
    async (files: File[]) => {
      setView('chat');
      const uploadFn = isMockMode() ? mockUploadFile : uploadFile;
      for (const file of files) {
        try {
          const res = await uploadFn(file);
          if (res.error) {
            console.error('上传失败:', res.error);
            continue;
          }
          runner.startTranslation({
            kind: 'file',
            title: file.name,
            fileId: res.file_id,
            sourcePreview: null,
          });
        } catch (err) {
          console.error('上传失败:', err);
        }
      }
    },
    [runner]
  );

  /** 术语确认提交 */
  const handleConfirmTerms = useCallback(
    (msgId: string, confirmed: Parameters<typeof runner.confirmPendingTerms>[1]) => {
      runner.confirmPendingTerms(msgId, confirmed);
    },
    [runner]
  );

  const handleSkipTerms = useCallback(
    (msgId: string) => {
      runner.skipPendingTerms(msgId);
    },
    [runner]
  );

  /** D8.1 MVP：确认译中初译（中英对照）→ 继续译后 */
  const handleConfirmDraft = useCallback(
    (msgId: string) => {
      runner.confirmDraft(msgId);
    },
    [runner]
  );

  /** 确认终稿：解锁导出 + 沉淀 + 完成 */
  const handleConfirmFinal = useCallback(
    (msgId: string) => {
      runner.confirmFinal(msgId);
    },
    [runner]
  );

  /** 在右栏打开文件（自动展开右栏） */
  const handleOpenFile = useCallback(
    (fileId: string) => {
      setSelectedFileId(fileId);
      setRightPanelOpen(true);
    },
    []
  );

  /** 左栏功能区动作（create/settings 由 LeftPanel 直接处理） */
  const handleFeature = useCallback(
    (key: string) => {
      setView('chat');
      const files = activeProject?.files ?? [];
      if (key === 'terms') {
        const f = files.find((x) => x.kind === 'terms');
        if (f) {
          setSelectedFileId(f.id);
          setRightPanelOpen(true);
        } else {
          alert('当前项目还没有术语表——完成一次翻译并确认术语后生成');
        }
      } else if (key === 'tm') {
        const f = files.find((x) => x.kind === 'tm');
        if (f) {
          setSelectedFileId(f.id);
          setRightPanelOpen(true);
        } else {
          alert('翻译记忆由后端知识库管理，当前项目暂无记录');
        }
      } else {
        console.warn(`[Feature] ${key} 暂未实现`);
      }
    },
    [activeProject]
  );

  if (!projectId) {
    return null;
  }

  return (
    <div className="workspace-shell">
      {/* ── 顶条 ── */}
      <div className="workspace-topbar">
        <Button
          className="workspace-new-btn"
          variant="ghost"
          size="icon"
          onClick={handleCreateProject}
          title="新建项目"
        >
          <Icon icon={Plus} size={16} />
        </Button>
        <span className="workspace-title">{activeProject?.name ?? '未命名项目'}</span>
        <Badge variant={isMock ? 'warning' : 'success'} className="workspace-mode">
          {isMock ? 'Mock' : '真实'}
        </Badge>
        <Button
          className={`topbar-panel-btn ${rightPanelOpen ? 'active' : ''}`}
          variant={rightPanelOpen ? 'secondary' : 'outline'}
          size="sm"
          onClick={handleToggleRight}
          icon={<Icon icon={PanelRightOpen} size={14} />}
        >
          文件
        </Button>
      </div>

      {/* ── 三栏 body ── */}
      <div className="workspace-body">
        <LeftPanel
          navigateOnSelect
          onFeature={handleFeature}
          onOpenSettings={handleOpenSettings}
        />

        {/* 中栏：对话流 / 设置 视图 */}
        {view === 'settings' ? (
          <SettingsPanel onSettingsChange={setLlmSettings} />
        ) : (
          <CenterPanel
            messages={activeProject?.messages ?? []}
            files={activeProject?.files ?? []}
            projectId={projectId}
            busy={runner.busy}
            onSendText={handleSendText}
            onUploadFiles={handleUploadFiles}
            onAbort={runner.abort}
            onConfirmTerms={handleConfirmTerms}
            onSkipTerms={handleSkipTerms}
            onOpenFile={handleOpenFile}
            onConfirmFinal={handleConfirmFinal}
            onConfirmDraft={handleConfirmDraft}
            onSkipDraft={handleConfirmDraft}
          />
        )}

        {/* 右栏：文件阅览区 / 设置预览 */}
        {rightPanelOpen &&
          (view === 'settings' ? (
            <SettingsSummary
              settings={llmSettings}
              onClose={() => setRightPanelOpen(false)}
            />
          ) : (
            <RightPanel
              files={activeProject?.files ?? []}
              selectedFileId={selectedFileId}
              onSelect={(file: ProjectFile) => setSelectedFileId(file.id)}
              onClose={() => setRightPanelOpen(false)}
            />
          ))}
      </div>
    </div>
  );
}
