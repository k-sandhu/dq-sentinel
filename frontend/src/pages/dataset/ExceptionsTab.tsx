import ExceptionsTriage from "../../components/ExceptionsTriage";

export default function ExceptionsTab({ datasetId }: { datasetId: number }) {
  return <ExceptionsTriage datasetId={datasetId} />;
}
