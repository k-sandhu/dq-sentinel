import { useQuery } from "@tanstack/react-query";
import { Navigate, NavLink, useParams } from "react-router";
import { api } from "../api/client";
import type { DocContent, DocSummary } from "../api/types";
import InfoShell from "../components/InfoShell";
import Markdown from "../components/Markdown";
import { EmptyState, ErrorBox, Spinner } from "../components/ui";

function formatUpdated(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

function DocView({ slug }: { slug: string }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["doc", slug],
    queryFn: () => api.get<DocContent>(`/docs/${slug}`),
  });

  if (isLoading) return <Spinner label="Loading document…" />;
  if (error) return <ErrorBox error={error} />;
  if (!data) return null;

  return (
    <article className="docs-content card card-pad">
      <div className="doc-meta">
        <h1>{data.title}</h1>
        <span className="sub">Updated {formatUpdated(data.updated_at)}</span>
      </div>
      <Markdown>{data.markdown}</Markdown>
    </article>
  );
}

export default function DocsPage() {
  const { slug } = useParams();
  const { data: docs, isLoading, error } = useQuery({
    queryKey: ["docs"],
    queryFn: () => api.get<DocSummary[]>("/docs"),
  });

  // No slug in the URL: land on the first doc so the page is never blank
  // (and the URL stays shareable).
  if (!slug && docs && docs.length > 0) {
    return <Navigate to={`/docs/${docs[0].slug}`} replace />;
  }

  return (
    <InfoShell>
      <div className="page-header">
        <div>
          <h1>Documentation</h1>
          <div className="sub">Project docs, rendered from the repository's docs folder</div>
        </div>
      </div>

      <ErrorBox error={error} />

      {isLoading ? (
        <Spinner label="Loading docs…" />
      ) : !docs || docs.length === 0 ? (
        <EmptyState
          title="No documentation found"
          hint="No markdown files were found in the docs folder. Add files under docs/ (and, in Docker, ensure ./docs is mounted)."
        />
      ) : (
        <div className="docs-layout">
          <nav className="docs-rail card" aria-label="Documents">
            {docs.map((d) => (
              <NavLink
                key={d.slug}
                to={`/docs/${d.slug}`}
                className={({ isActive }) => `docs-rail-item${isActive ? " active" : ""}`}
                title={d.title}
              >
                {d.title}
              </NavLink>
            ))}
          </nav>
          {slug ? (
            <DocView slug={slug} />
          ) : (
            <div className="docs-content">
              <EmptyState title="Pick a document" hint="Choose a doc from the list to start reading." />
            </div>
          )}
        </div>
      )}
    </InfoShell>
  );
}
