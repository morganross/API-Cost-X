import { lazy, Suspense } from 'react'
import { Routes, Route, Navigate } from 'react-router-dom'
import Layout from './pages/Layout'
import ExecutionHistory from './pages/ExecutionHistory'
import Execute from './pages/Execute'
import Settings from './pages/Settings'
import Configure from './pages/Configure'
import Presets from './pages/Presets'
import ContentLibrary from './pages/ContentLibrary'
import { NotificationContainer } from './components/ui/notification'
import { experimentDefinitions } from './experiments/registry'

const Quality = lazy(() => import('./pages/Quality'))

function App() {
  return (
    <>
      <NotificationContainer />
      <Routes>
        <Route path="/" element={<Layout />}>
          <Route index element={<Navigate to="/quality" replace />} />
          <Route path="configure" element={<Configure />} />
          <Route path="presets" element={<Presets />} />
          <Route path="presets14" element={<Navigate to="/presets" replace />} />
          {experimentDefinitions.map((experiment) => {
            const ExperimentComponent = experiment.component

            return (
              <Route
                key={experiment.id}
                path={experiment.path}
                element={
                  <Suspense
                    fallback={
                      <div
                        style={{
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'center',
                          height: '100vh',
                          color: '#9ca3af',
                        }}
                      >
                        Loading…
                      </div>
                    }
                  >
                    <ExperimentComponent />
                  </Suspense>
                }
              />
            )
          })}
          <Route path="content" element={<ContentLibrary />} />
          <Route path="execute" element={<Execute />} />
          <Route path="execute/:runId" element={<Execute />} />
          <Route path="history" element={<ExecutionHistory />} />
          <Route path="evaluation" element={<Navigate to="/" replace />} />
          <Route path="quality" element={<Suspense fallback={<div style={{display:"flex",alignItems:"center",justifyContent:"center",height:"100vh",color:"#9ca3af"}}>Loading…</div>}><Quality /></Suspense>} />
          <Route path="settings" element={<Settings />} />
            <Route path="github" element={<Navigate to="/settings" replace />} />
        </Route>
      </Routes>
    </>
  )
}

export default App
