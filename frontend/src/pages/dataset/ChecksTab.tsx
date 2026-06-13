import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../../api/client";
import type { Check, GenerateResult, Health } from "../../api/types";
import { canEdit, useAuth } from "../../auth";
import ChecksTable from "../../components/ChecksTable";
import NewCheckModal from "../../components/NewCheckModal";
import { ErrorBox, Icon, Spinner } from "../../components/ui";

export default function ChecksTab({ datasetId, hasProfile }: { datasetId: number; hasProfile: boolean }) {
  const { user } = useAuth();
  const qc = useQueryClient();
  const [creating, setCreating] = useState(false);
  const [explore, setExplore] = useState(false);
  const [genResult, setGenResult] = useState<GenerateResult | null>(null);

  const { data: health } = useQuery({ queryKey: ["health"], queryFn: () => api.get<Health>("/health") });
  const llm = health?.llm_enabled ?? false;

  const { data: checks, isLoading, error } = useQuery({
    queryKey: ["checks", { datasetId }],
    queryFn: () => api.get<Check[]>(`/checks?dataset_id=${datasetId}`),
  });

  const generate = useMutation({
    mutationFn: () =>
      api.post<GenerateResult>("/checks/generate", {
        dataset_id: datasetId,
        use_llm: llm,
        explore: llm && explore,
      }),
    onSuccess: (result) => {
      setGenResult(result);
      qc.invalidateQueries({ queryKey: ["checks"] });
      qc.invalidateQueries({ queryKey: ["exploration", datasetId] });
    },
  });

  return (
    <div>
      {canEdit(user) && (
        <div className="toolbar">
          <button
            className="primary"
            onClick={() => generate.mutate()}
            disabled={generate.isPending || !hasProfile}
            title={!hasProfile ? "Profile the dataset first" : undefined}
          >
            {generate.isPending ? <span className="spinner" style={{ width: 13, height: 13 }} /> : <Icon name="bolt" size={14} />}
            {generate.isPending
              ? explore && llm
                ? "Exploring data & generating..."
                : "Generating..."
              : llm
                ? "Generate checks (AI)"
                : "Generate checks"}
          </button>
          {llm && (
            <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12.5, fontWeight: 600 }}>
              <input
                type="checkbox"
                checked={explore}
                onChange={(e) => setExplore(e.target.checked)}
                style={{ width: "auto", marginTop: 0 }}
              />
              Explore data first (agent runs SQL to learn more)
            </label>
          )}
          {!llm && <span className="badge" title="Set DQ_LLM_API_KEY + DQ_LLM_MODEL or ANTHROPIC_API_KEY for AI generation">heuristic mode</span>}
          <div className="right">
            <button onClick={() => setCreating(true)}>
              <Icon name="plus" size={13} /> New check
            </button>
          </div>
        </div>
      )}
      <ErrorBox error={error || generate.error} />
      {genResult && (
        <div className="info-box">
          Generated <strong>{genResult.created}</strong> proposal{genResult.created === 1 ? "" : "s"} via{" "}
          <strong>{genResult.mode === "llm" ? "AI" : "heuristics"}</strong>
          {genResult.explored ? " after exploring the data" : ""}
          {genResult.skipped_duplicates ? ` (${genResult.skipped_duplicates} duplicates skipped)` : ""}. Review and activate them below.
        </div>
      )}
      {isLoading ? <Spinner /> : <ChecksTable checks={checks ?? []} showDataset={false} />}
      {creating && <NewCheckModal datasetId={datasetId} onClose={() => setCreating(false)} />}
    </div>
  );
}
