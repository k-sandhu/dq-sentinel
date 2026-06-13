// Thin wrapper preserving the original embedding contract {datasetId?, runId?,
// checkId?} — used by the dataset Exceptions tab and run drill-ins. The real
// workspace (saved views, filter bar, side panel, keyboard triage) lives in
// components/exceptions/** (#63).

import ExceptionsWorkspace from "./exceptions/ExceptionsWorkspace";

export default function ExceptionsTriage({
  datasetId,
  runId,
  checkId,
}: {
  datasetId?: number;
  runId?: number;
  checkId?: number;
}) {
  return <ExceptionsWorkspace datasetId={datasetId} runId={runId} checkId={checkId} />;
}
