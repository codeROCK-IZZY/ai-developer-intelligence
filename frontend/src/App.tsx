import type { ReactNode } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import type { Role } from "./api/types";
import { Layout } from "./components/Layout";
import { Loading } from "./components/ui";
import { useAuth } from "./lib/auth";
import { AdminPage } from "./pages/AdminPage";
import { AskPage } from "./pages/AskPage";
import { LoginPage } from "./pages/LoginPage";
import { RepositoriesPage } from "./pages/RepositoriesPage";
import { RepositoryPage } from "./pages/RepositoryPage";
import { SearchPage } from "./pages/SearchPage";
import { SymbolPage } from "./pages/SymbolPage";
import { WorkspacesPage } from "./pages/WorkspacesPage";

function RequireRole({ role, children }: { role: Role; children: ReactNode }) {
  const { can } = useAuth();
  return can(role) ? <>{children}</> : <p className="text-sm text-slate-600">You don't have access to this page.</p>;
}

export function App() {
  const { user, loading } = useAuth();
  if (loading) return <Loading />;
  if (!user) return <Routes><Route path="*" element={<LoginPage />} /></Routes>;
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<RepositoriesPage />} />
        <Route path="repositories/:id" element={<RepositoryPage />} />
        <Route path="symbols/:id" element={<SymbolPage />} />
        <Route path="search" element={<SearchPage />} />
        <Route path="ask" element={<AskPage />} />
        <Route path="workspaces" element={<WorkspacesPage />} />
        <Route path="admin" element={<RequireRole role="admin"><AdminPage /></RequireRole>} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
