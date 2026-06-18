import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";
import { api } from "../api/client";
import InfoShell from "../components/InfoShell";
import { Icon } from "../components/ui";
import { FEATURE_SECTIONS } from "../lib/features";

interface HealthOut {
  version?: string;
  llm_enabled?: boolean;
}

export default function FeaturesPage() {
  // Lightweight, public endpoint — used only to stamp version + AI status.
  const { data: health } = useQuery({
    queryKey: ["health"],
    queryFn: () => api.get<HealthOut>("/health"),
    staleTime: 60_000,
  });

  const featureCount = FEATURE_SECTIONS.reduce((n, s) => n + s.items.length, 0);

  return (
    <InfoShell>
      <div className="page-header">
        <div>
          <h1>Features</h1>
          <div className="sub">
            Everything built into DQ Sentinel so far — {featureCount} capabilities across{" "}
            {FEATURE_SECTIONS.length} areas
          </div>
        </div>
        <div className="header-actions">
          {health?.version && <span className="pill tone-neutral">v{health.version}</span>}
          <span className={`pill ${health?.llm_enabled ? "tone-ok" : "tone-neutral"}`}>
            <Icon name="sparkles" size={13} />
            AI {health?.llm_enabled ? "enabled" : "available (no key)"}
          </span>
        </div>
      </div>

      <div className="features-grid">
        {FEATURE_SECTIONS.map((section) => (
          <section key={section.title} className="feature-section card card-pad">
            <div className="feature-section-head">
              <span className="feature-section-icon">
                <Icon name={section.icon} size={18} />
              </span>
              <div>
                <h2>{section.title}</h2>
                <div className="sub">{section.blurb}</div>
              </div>
            </div>
            <ul className="feature-list">
              {section.items.map((item) => (
                <li key={item.title} className="feature-item">
                  <div className="feature-item-head">
                    <span className="feature-item-title">{item.title}</span>
                    {item.ai && <span className="pill tone-accent pill-outline">AI</span>}
                  </div>
                  <p className="feature-item-desc">{item.description}</p>
                  {(item.to || item.docSlug) && (
                    <div className="feature-item-links">
                      {item.to && (
                        <Link className="feature-link" to={item.to}>
                          Open <Icon name="arrow-right" size={12} />
                        </Link>
                      )}
                      {item.docSlug && (
                        <Link className="feature-link" to={`/docs/${item.docSlug}`}>
                          <Icon name="book" size={12} /> Docs
                        </Link>
                      )}
                    </div>
                  )}
                </li>
              ))}
            </ul>
          </section>
        ))}
      </div>
    </InfoShell>
  );
}
