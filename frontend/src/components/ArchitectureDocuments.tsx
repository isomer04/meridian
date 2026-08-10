"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api/client";
import type { DocumentResponse, DocumentSlug } from "@/lib/api/generated";
import { MarkdownDocument } from "@/components/MarkdownDocument";

const DOCS: { slug: DocumentSlug; anchor: string }[] = [
  { slug: "architecture", anchor: "architecture-doc" },
  { slug: "assumptions", anchor: "assumptions-doc" },
];

function documentLabel(slug: DocumentSlug, document?: DocumentResponse) {
  return document?.title ?? slug[0].toUpperCase() + slug.slice(1);
}

function HierarchyDiagram() {
  return (
    <section
      className="architecture-overview panel"
      aria-labelledby="architecture-overview-title"
    >
      <p className="eyebrow">CONTROL HIERARCHY</p>
      <h2 id="architecture-overview-title">Who leads the system?</h2>
      <p>
        Control belongs to application code. The LLM agents are three peer
        specialists that provide bounded judgment when the workflow asks for
        it.
      </p>
      <div className="architecture-leader">
        <p className="eyebrow">
          System leader · deterministic code · not an LLM
        </p>
        <strong>OriginationFlow</strong>
        <p>
          Owns sequence, routing, state, tool execution, approvals, and
          recovery.
        </p>
      </div>
      <div className="architecture-arrow" aria-hidden="true">
        ↓ invokes judgment at defined workflow steps
      </div>
      <div className="architecture-agents">
        <p className="eyebrow">
          Three peer LLM agents · no manager agent
        </p>
        <div className="architecture-agent-grid">
          <div className="architecture-agent">
            <strong>Collateral Agent</strong>
            <span>Judges value acceptance and collateral evidence.</span>
          </div>
          <div className="architecture-agent">
            <strong>Underwriter Agent</strong>
            <span>Reconciles AUS findings with lender overlays.</span>
          </div>
          <div className="architecture-agent">
            <strong>Compliance &amp; QC Agent</strong>
            <span>Reviews citations, rationale, and compliance quality.</span>
          </div>
        </div>
      </div>
      <div className="architecture-control-grid">
        <div className="architecture-control">
          <strong>Deterministic work</strong>
          <span>Validation, calculations, retrieval, and routing.</span>
        </div>
        <div className="architecture-control">
          <strong>Guarded side effects</strong>
          <span>Tool gateway, policy checks, saga, and durable database.</span>
        </div>
        <div className="architecture-control">
          <strong>Human authority</strong>
          <span>Named approvers decide gates G1–G4.</span>
        </div>
      </div>
      <p className="notice">
        The Underwriter Agent makes the core loan judgment, but it does not
        lead the other agents or decide what runs next. OriginationFlow does.
      </p>
    </section>
  );
}

export function ArchitectureDocuments() {
  const [documents, setDocuments] = useState<Record<string, DocumentResponse>>({});
  const [errors, setErrors] = useState<Partial<Record<DocumentSlug, string>>>({});
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    Promise.allSettled(DOCS.map(({ slug }) => api.document(slug))).then(
      (results) => {
        const nextDocuments: Record<string, DocumentResponse> = {};
        const nextErrors: Partial<Record<DocumentSlug, string>> = {};
        results.forEach((result, index) => {
          const slug = DOCS[index].slug;
          if (result.status === "fulfilled") nextDocuments[slug] = result.value;
          else
            nextErrors[slug] =
              result.reason instanceof Error
                ? result.reason.message
                : `Could not load ${documentLabel(slug)}.`;
        });
        setDocuments(nextDocuments);
        setErrors(nextErrors);
        setLoaded(true);
      },
    );
  }, []);

  return (
    <>
      <p className="eyebrow">ARCHITECTURE</p>
      <h1>Control hierarchy</h1>
      <div className="architecture-layout">
        <nav
          className="architecture-contents"
          aria-label="Document contents"
        >
          <p className="eyebrow">Contents</p>
          <ul>
            <li>
              <a href="#architecture-overview-title">Control hierarchy</a>
            </li>
            {DOCS.map(({ slug, anchor }) => (
              <li key={slug}>
                <a href={`#${anchor}`}>{documentLabel(slug, documents[slug])}</a>
              </li>
            ))}
          </ul>
        </nav>
        <div className="architecture-content">
          <HierarchyDiagram />
          {DOCS.map(({ slug, anchor }) => {
            const doc = documents[slug];
            return (
              <section
                key={slug}
                id={anchor}
                className="panel section"
                aria-labelledby={`${anchor}-heading`}
              >
                <p className="eyebrow" id={`${anchor}-heading`}>
                  {documentLabel(slug, doc)}
                </p>
                {doc ? (
                  <MarkdownDocument markdown={doc.markdown} />
                ) : errors[slug] ? (
                  <p className="notice error" role="alert">
                    {errors[slug]}
                  </p>
                ) : loaded ? (
                  <p className="notice error" role="alert">
                    Could not load {documentLabel(slug)}.
                  </p>
                ) : (
                  <p role="status">Loading {slug}…</p>
                )}
              </section>
            );
          })}
        </div>
      </div>
    </>
  );
}
