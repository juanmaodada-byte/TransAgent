/**
 * AutoWorkspaceGate
 * ==================
 * 首页入口：自动进入工作台。
 *   有项目 → 进入最近激活的项目
 *   无项目 → 自动创建「默认项目」并进入
 */

import { useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { useProjectsContext } from '../context/ProjectsContext';

export function AutoWorkspaceGate() {
  const navigate = useNavigate();
  const { projects, activeProjectId, actions } = useProjectsContext();
  const doneRef = useRef(false);

  useEffect(() => {
    if (doneRef.current) return;
    doneRef.current = true;

    if (projects.length === 0) {
      // 无项目 → 自动创建默认项目
      const id = actions.createProject('默认项目');
      navigate(`/projects/${id}`, { replace: true });
    } else {
      // 有项目 → 进入最近激活的（或第一个）
      const id = activeProjectId ?? projects[0].id;
      if (id !== activeProjectId) {
        actions.selectProject(id);
      }
      navigate(`/projects/${id}`, { replace: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return null;
}
