import { Alert, Button } from "antd";

/** The inline error state every data view uses (DESIGN.md, Layout). */
export function LoadError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <Alert
      type="error"
      title={message}
      action={
        <Button size="small" onClick={onRetry}>
          Retry
        </Button>
      }
    />
  );
}
