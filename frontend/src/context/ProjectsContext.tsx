/**
 * ProjectsContext
 * ================
 * 全局项目状态上下文：App 层注入，WelcomePage / WorkspacePage / 各面板共享。
 */

import { createContext, useContext, type ReactNode } from 'react';
import { useProjects, type UseProjectsReturn } from '../hooks/useProjects';

const ProjectsContext = createContext<UseProjectsReturn | null>(null);

export function ProjectsProvider({ children }: { children: ReactNode }) {
  const value = useProjects();
  return (
    <ProjectsContext.Provider value={value}>
      {children}
    </ProjectsContext.Provider>
  );
}

export function useProjectsContext(): UseProjectsReturn {
  const ctx = useContext(ProjectsContext);
  if (!ctx) {
    throw new Error('useProjectsContext 必须在 <ProjectsProvider> 内使用');
  }
  return ctx;
}
