"use client";

import type { ProjectSummary } from "@/lib/types";

import { Badge, Card } from "./ui";

export function ProjectList({ projects, selected, onSelect }: {
  projects: ProjectSummary[];
  selected: string | null;
  onSelect: (name: string) => void;
}) {
  return (
    <Card title="Projects">
      {projects.length === 0 && <p className="text-sm text-zinc-500">No projects yet. Analyze a repository to start.</p>}
      <ul className="flex flex-col gap-1">
        {projects.map((p) => (
          <li key={p.name}>
            <button
              onClick={() => onSelect(p.name)}
              className={`w-full rounded-lg px-3 py-2 text-left hover:bg-zinc-800 ${selected === p.name ? "bg-zinc-800" : ""}`}
            >
              <div className="text-sm font-medium text-zinc-100">{p.plan_title ?? p.repository_name ?? p.name}</div>
              <div className="mt-1 flex flex-wrap gap-1">
                <Badge color={p.has_analysis ? "green" : "gray"}>analysis</Badge>
                <Badge color={p.has_plan ? "green" : "gray"}>plan</Badge>
                <Badge color={p.script_sections === 0 ? "gray" : p.script_sections >= p.plan_sections ? "green" : "yellow"}>
                  script {p.script_sections}/{p.plan_sections || "?"}
                </Badge>
              </div>
            </button>
          </li>
        ))}
      </ul>
    </Card>
  );
}
