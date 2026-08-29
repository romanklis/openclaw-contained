'use client'

import { createContext, useContext, useEffect, useState, ReactNode } from 'react'
import { API } from './api'

export interface Project {
  id: string
  name: string
  description?: string
}

interface ProjectContextValue {
  projects: Project[]
  activeProject: string   // '' = all, '__general__' = unassigned, else project id
  setActiveProject: (id: string) => void
}

const ProjectContext = createContext<ProjectContextValue>({
  projects: [],
  activeProject: '',
  setActiveProject: () => {},
})

const STORAGE_KEY = 'openclaw.activeProject'

export function ProjectProvider({ children }: { children: ReactNode }) {
  const [projects, setProjects] = useState<Project[]>([])
  const [activeProject, setActiveProjectState] = useState<string>('')

  useEffect(() => {
    try {
      const saved = window.localStorage.getItem(STORAGE_KEY)
      if (saved) setActiveProjectState(saved)
    } catch { /* ignore */ }
    fetch(`${API}/api/projects`)
      .then((r) => r.json())
      .then((data: Project[]) => setProjects(Array.isArray(data) ? data : []))
      .catch(() => {})
  }, [])

  const setActiveProject = (id: string) => {
    setActiveProjectState(id)
    try {
      window.localStorage.setItem(STORAGE_KEY, id)
    } catch { /* ignore */ }
  }

  return (
    <ProjectContext.Provider value={{ projects, activeProject, setActiveProject }}>
      {children}
    </ProjectContext.Provider>
  )
}

export function useProject() {
  return useContext(ProjectContext)
}
