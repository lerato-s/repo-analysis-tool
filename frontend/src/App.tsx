import { lazy, Suspense } from "react";
import { Link, NavLink, Route, Routes } from "react-router-dom";
import Repos from "./pages/Repos";
import { Empty, Spinner } from "./ui";

// The dashboard (and its charting bundle) only loads once a repo is opened.
const RepoDashboard = lazy(() => import("./pages/RepoDashboard"));

function NotFound() {
  return (
    <Empty title="Page not found" hint="That link does not point anywhere.">
      <Link className="btn" to="/">
        Back to repositories
      </Link>
    </Empty>
  );
}

export default function App() {
  return (
    <div className="app">
      <header className="topbar">
        <Link className="brand" to="/">
          <span className="brand-mark">RAT</span>
          <span className="brand-name">Repo Analysis Tool</span>
        </Link>
        <nav className="nav">
          <NavLink to="/" end className={({ isActive }) => (isActive ? "active" : "")}>
            Repositories
          </NavLink>
        </nav>
      </header>
      <main className="container">
        <Suspense fallback={<Spinner label="Loading dashboard…" />}>
          <Routes>
            <Route path="/" element={<Repos />} />
            <Route path="/r/:repoId" element={<RepoDashboard />} />
            <Route path="*" element={<NotFound />} />
          </Routes>
        </Suspense>
      </main>
      <footer className="footer muted">
        Repo Analysis Tool · metrics computed from <code>git log</code> with 50% rename detection
      </footer>
    </div>
  );
}
