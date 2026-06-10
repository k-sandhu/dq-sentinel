import { useQuery } from "@tanstack/react-query";
import { api } from "../../api/client";
import type { Run } from "../../api/types";
import RunsTable from "../../components/RunsTable";
import { ErrorBox, Spinner } from "../../components/ui";

export default function RunsTab({ datasetId }: { datasetId: number }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["runs", { datasetId }],
    queryFn: () => api.get<Run[]>(`/runs?dataset_id=${datasetId}&limit=100`),
    refetchInterval: 20_000,
  });
  if (isLoading) return <Spinner />;
  return (
    <div className="card">
      <ErrorBox error={error} />
      <RunsTable runs={data ?? []} showDataset={false} />
    </div>
  );
}
