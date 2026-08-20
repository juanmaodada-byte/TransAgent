/**
 * TransAgent App 根组件
 * =====================
 * 三栏项目工作区路由：
 *   /                      → WelcomePage（新建/最近项目 + 输入入口）
 *   /projects/:projectId   → WorkspacePage（三栏工作区）
 *   旧 /translate/:id       → 重定向到 /
 */

import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { ProjectsProvider } from './context/ProjectsContext';
import { AutoWorkspaceGate } from './pages/AutoWorkspaceGate';
import { WorkspacePage } from './pages/WorkspacePage/WorkspacePage';
import './App.css';

function App() {
  return (
    <ProjectsProvider>
      <BrowserRouter>
        <Routes>
          {/* 首页 → 自动进入工作台（无项目自动创建默认项目） */}
          <Route path="/" element={<AutoWorkspaceGate />} />
          <Route path="/projects/:projectId" element={<WorkspacePage />} />
          {/* 旧单文档路由重定向 */}
          <Route path="/translate/:sessionId" element={<Navigate to="/" replace />} />
          {/* 未匹配 → 首页 */}
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </ProjectsProvider>
  );
}

export default App;
