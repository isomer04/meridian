import { CaseWorkspace } from "@/components/CaseWorkspace";
export default async function CasePage({
  params,
}: {
  params: Promise<{ runId: string }>;
}) {
  const { runId } = await params;
  return <CaseWorkspace runId={runId} />;
}
