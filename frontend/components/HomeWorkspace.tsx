"use client";

import { useState } from "react";
import { NewRunForm } from "@/components/NewRunForm";
import { RepoSidebar, type Selection } from "@/components/RepoSidebar";

export function HomeWorkspace() {
  const [selection, setSelection] = useState<Selection | null>(null);
  return (
    <div className="home-grid">
      <div>
        <section aria-labelledby="intro-h">
          <h1 id="intro-h">A release agent that can crash and still not publish twice.</h1>
          <p className="lede">
            RecoveryOS prepares a release in seven steps and stops for a person before anything is
            published. Every step is saved as it finishes, so a crash, a timeout or a rate limit
            resumes from the last good checkpoint.
          </p>
          <ul className="proof">
            <li>
              <span className="mono">LLM</span>
              <span>
                Each step is attempted by a language model first. If it is unavailable or its answer
                contradicts the facts, deterministic code takes over, and the page says which one
                did the work and why.
              </span>
            </li>
            <li>
              <span className="mono">Once</span>
              <span>
                Side effects carry a key. A retry, a resume or a lost reply cannot create a second
                draft or a second release.
              </span>
            </li>
            <li>
              <span className="mono">Gate</span>
              <span>
                Approval is checked again at the moment of publishing. Revoked, expired or for a
                different plan means no release.
              </span>
            </li>
          </ul>
        </section>
        <RepoSidebar selection={selection} />
      </div>
      <NewRunForm onSelect={setSelection} />
    </div>
  );
}
