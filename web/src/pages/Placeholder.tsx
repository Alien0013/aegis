// Temporary page for routes not yet rebuilt. Keeps the app fully navigable and
// compiling while pages are ported one phase at a time.

import { Card, Empty, PageHeader } from "../components/ui";

export function Placeholder({ title }: { title: string }) {
  return (
    <>
      <PageHeader title={title} sub="Being rebuilt" />
      <Card>
        <Empty icon="tools">
          The <b className="text-dim">{title}</b> page is being rebuilt in the new dashboard.
        </Empty>
      </Card>
    </>
  );
}
