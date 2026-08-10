import { Suspense } from "react";
import { ApprovalWorkbench } from "@/components/ApprovalWorkbench";

export default function ApprovalsPage() {
  return (
    <Suspense
      fallback={
        <>
          <h1>Human gate workbench</h1>
          <p role="status">Loading the approval queue…</p>
        </>
      }
    >
      <ApprovalWorkbench />
    </Suspense>
  );
}
