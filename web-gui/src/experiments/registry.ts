import { lazy, type ComponentType, type LazyExoticComponent } from 'react'
import { Workflow, type LucideIcon } from 'lucide-react'

export interface ExperimentDefinition {
  id: string
  name: string
  href: string
  path: string
  icon: LucideIcon
  component: LazyExoticComponent<ComponentType>
}

export const experimentDefinitions: ExperimentDefinition[] = [
  {
    id: 'react-flow-mvp',
    name: 'Flow Lab',
    href: '/flow-lab',
    path: 'flow-lab',
    icon: Workflow,
    component: lazy(() => import('./react-flow-mvp/page/ReactFlowMvpPage')),
  },
]

export const experimentNavigation = experimentDefinitions.map(({ id, name, href, icon }) => ({
  id,
  name,
  href,
  icon,
}))
