"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { Activity, BarChart3, Inbox, Layers3, Menu, PlayCircle } from "lucide-react";
import { ApiError, api } from "@/lib/api/client";
import type { SystemState } from "@/lib/api/generated";

const links = [
  ["/loans/new", "Run a loan", PlayCircle],
  ["/approvals", "Approval queue", Inbox],
  ["/evaluation", "Evaluation", BarChart3],
  ["/architecture", "Architecture", Layers3],
] as const;

const routeTitles: Record<string, string> = {
  "/loans/new": "Run a loan",
  "/approvals": "Human gate workbench",
  "/evaluation": "Evaluation",
  "/architecture": "Architecture",
};

export function Shell({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const [system, setSystem] = useState<SystemState>();
  const [error, setError] = useState(false);
  const [loading, setLoading] = useState(true);
  const mobileNav = useRef<HTMLDetailsElement>(null);
  const previousPath = useRef(path);

  async function loadSystem() {
    setLoading(true);
    try {
      setSystem(await api.system());
      setError(false);
    } catch (reason) {
      setError(reason instanceof ApiError);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (mobileNav.current) mobileNav.current.open = false;
    void Promise.resolve().then(loadSystem);
    if (previousPath.current !== path) {
      requestAnimationFrame(() => document.getElementById("main")?.focus());
    }
    previousPath.current = path;
  }, [path]);

  const nav = (
    <nav aria-label="Primary navigation" className="nav">
      {links.map(([href, label, Icon]) => (
        <Link
          key={href}
          href={href}
          aria-label={label}
          aria-current={path === href || (href === "/loans/new" && path.startsWith("/loans/runs/")) ? "page" : undefined}
        >
          <Icon aria-hidden="true" size={21} strokeWidth={1.8} />
          <span>{label}</span>
        </Link>
      ))}
    </nav>
  );

  return (
    <div className="shell">
      <a className="skip" href="#main">Skip to main content</a>
      <aside className="rail" aria-label="Meridian command rail">
        <Link href="/loans/new" className="wordmark" aria-label="Meridian home">M</Link>
        {nav}
        <div className="rail-runtime" title={loading ? "Checking API" : error ? "API unavailable" : "API connected"}>
          <span className={`marker ${error ? "error" : ""}`} aria-hidden="true" />
          <span className="visually-hidden">{loading ? "Checking Python API" : error ? "Python API unavailable" : "API connected"}</span>
        </div>
      </aside>
      <header className="command-bar">
        <div className="command-title">{routeTitles[path] ?? "Active loan run"}</div>
        <div className="command-status" aria-live="polite">
          <span className={`marker ${error ? "error" : ""}`} aria-hidden="true" />
          {loading ? "Checking Python API" : error ? "Python API unavailable" : "API connected"}
          {error && <button className="command-retry" onClick={() => void loadSystem()}>Retry connection</button>}
          {system?.active_run_id && (
            <Link className="active-run" href={`/loans/runs/${system.active_run_id}`}>
              <Activity aria-hidden="true" size={16} /> View active run
            </Link>
          )}
        </div>
      </header>
      <header className="mobile-nav">
        <Link href="/loans/new" className="mobile-brand" aria-label="Meridian home">M</Link>
        <strong>MERIDIAN</strong>
        <details ref={mobileNav}>
          <summary><Menu aria-hidden="true" size={20} /> Navigation</summary>
          {nav}
          <div className="mobile-runtime" aria-live="polite">
            <span className={`marker ${error ? "error" : ""}`} aria-hidden="true" />
            {loading ? "Checking Python API" : error ? "Python API unavailable" : "API connected"}
            {error && <button className="command-retry" onClick={() => void loadSystem()}>Retry connection</button>}
            {system?.active_run_id && <Link href={`/loans/runs/${system.active_run_id}`}>View active run</Link>}
          </div>
        </details>
      </header>
      <main
        id="main"
        tabIndex={-1}
        className={`main ${path === "/approvals" ? "main-approvals" : "main-standard"}`}
      >
        {children}
      </main>
    </div>
  );
}
