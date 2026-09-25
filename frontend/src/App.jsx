import { Link, Route, Routes } from 'react-router-dom'
import ProjectListPage from './pages/ProjectListPage.jsx'
import EditorPage from './pages/EditorPage.jsx'
import MergePage from './pages/MergePage.jsx'
import RulesPage from './pages/RulesPage.jsx'
import QualityPage from './pages/QualityPage.jsx'
import SpeakersPage from './pages/SpeakersPage.jsx'
import ExcerptsPage from './pages/ExcerptsPage.jsx'
import PublicTranscriptPage from './pages/PublicTranscriptPage.jsx'
import CorrectionsPage from './pages/CorrectionsPage.jsx'
import CoveragePage from './pages/CoveragePage.jsx'

export default function App() {
  return (
    <div className="app">
      <header className="topbar">
        <Link to="/" className="brand">
          🎙️ 访谈音频校注
        </Link>
        <nav>
          <Link to="/">项目</Link>
          <Link to="/merge">三方合并</Link>
        </nav>
      </header>
      <main className="content">
        <Routes>
          <Route path="/" element={<ProjectListPage />} />
          <Route path="/projects/:id" element={<EditorPage />} />
          <Route path="/projects/:id/rules" element={<RulesPage />} />
          <Route path="/projects/:id/quality" element={<QualityPage />} />
          <Route path="/projects/:id/speakers" element={<SpeakersPage />} />
          <Route path="/projects/:id/excerpts" element={<ExcerptsPage />} />
          <Route path="/projects/:id/public" element={<PublicTranscriptPage />} />
          <Route path="/projects/:id/corrections" element={<CorrectionsPage />} />
          <Route path="/projects/:id/coverage" element={<CoveragePage />} />
          <Route path="/merge" element={<MergePage />} />
        </Routes>
      </main>
    </div>
  )
}
