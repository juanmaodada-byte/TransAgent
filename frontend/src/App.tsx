/**
 * TransAgent App 根组件
 * =====================
 * 路由配置 + 全局布局。
 */

import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { Header } from './components/Layout/Header';
import { UploadPage } from './pages/UploadPage/UploadPage';
import { TranslatePage } from './pages/TranslatePage/TranslatePage';
import './App.css';

function App() {
  return (
    <BrowserRouter>
      <div className="app-shell">
        <Header />
        <main className="app-main">
          <Routes>
            <Route path="/" element={<UploadPage />} />
            <Route path="/translate/:sessionId" element={<TranslatePage />} />
            {/* 未匹配路由 → 回到首页 */}
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  );
}

export default App;
